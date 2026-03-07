"""NFC/RFID reader controller — PN532 via I2C.

Reads NDEF text records from NFC tags when the system-update button is pressed.
I2C wiring: SDA = GPIO 2 (pin 3), SCL = GPIO 3 (pin 5).
"""

from log import get_logger

logger = get_logger(__name__)


class NFCController:
    """Reads NFC tags via PN532 over I2C on the Raspberry Pi 5."""

    def __init__(self, config: dict):
        self.config = config
        self._pn532 = None
        nfc_cfg = config.get("NFC", {})
        self._i2c_bus = nfc_cfg.get("i2c_bus", 1)

        try:
            import board
            import busio
            from adafruit_pn532.i2c import PN532_I2C

            i2c = busio.I2C(board.SCL, board.SDA)
            self._pn532 = PN532_I2C(i2c, debug=False)
            ic, ver, rev, support = self._pn532.firmware_version
            logger.info("PN532 found: firmware %d.%d", ver, rev)
            self._pn532.SAM_configuration()
        except (ImportError, Exception) as e:
            logger.info("NFC reader not available: %s", e)
            self._pn532 = None

    @property
    def available(self) -> bool:
        return self._pn532 is not None

    def read_tag(self, timeout: float = 2.0) -> str | None:
        """Block briefly and try to read an NDEF text record from a tag.

        Returns the text payload, or None if no tag / no NDEF text found.
        """
        if not self._pn532:
            logger.debug("NFC read skipped — no reader")
            return None

        uid = self._pn532.read_passive_target(timeout=timeout)
        if uid is None:
            logger.debug("No NFC tag detected")
            return None

        uid_hex = uid.hex()
        logger.info("NFC tag detected: UID=%s", uid_hex)

        # Try to read NDEF message from tag (simplified: read first 4 blocks)
        text = self._read_ndef_text(uid)
        if text:
            logger.info("NFC tag text: %s", text[:80])
        return text

    def _read_ndef_text(self, uid) -> str | None:
        """Attempt to read NDEF text record from an NTag2xx / MIFARE Ultralight."""
        try:
            # Read pages 4-15 (user data area on NTag213/215/216)
            raw = bytearray()
            for page in range(4, 16):
                data = self._pn532.ntag2xx_read_block(page)
                if data is None:
                    break
                raw.extend(data)

            if not raw:
                return None

            # Simple NDEF TLV parser: look for type 0x03 (NDEF message)
            return self._parse_ndef_text(raw)
        except Exception as e:
            logger.debug("NDEF read error: %s", e)
            return None

    @staticmethod
    def _parse_ndef_text(raw: bytearray) -> str | None:
        """Minimal NDEF text record parser."""
        i = 0
        while i < len(raw):
            tlv_type = raw[i]
            if tlv_type == 0x00:  # NULL TLV
                i += 1
                continue
            if tlv_type == 0xFE:  # Terminator
                break
            if i + 1 >= len(raw):
                break
            tlv_len = raw[i + 1]
            i += 2
            if tlv_type == 0x03 and tlv_len > 0:  # NDEF Message TLV
                ndef = raw[i:i + tlv_len]
                return _extract_text_from_ndef(ndef)
            i += tlv_len
        return None

    def cleanup(self):
        pass


def _extract_text_from_ndef(ndef: bytearray) -> str | None:
    """Extract text payload from first NDEF record."""
    if len(ndef) < 3:
        return None
    # Record header
    header = ndef[0]
    type_len = ndef[1]
    sr = header & 0x10  # Short Record flag
    if sr:
        payload_len = ndef[2]
        offset = 3 + type_len
    else:
        if len(ndef) < 6:
            return None
        payload_len = int.from_bytes(ndef[2:6], "big")
        offset = 6 + type_len

    if offset >= len(ndef):
        return None

    payload = ndef[offset:offset + payload_len]
    if not payload:
        return None

    # Text record: first byte = status (encoding + language length)
    status = payload[0]
    lang_len = status & 0x3F
    text_bytes = payload[1 + lang_len:]
    encoding = "utf-8" if (status & 0x80) == 0 else "utf-16"
    try:
        return text_bytes.decode(encoding)
    except (UnicodeDecodeError, ValueError):
        return text_bytes.decode("utf-8", errors="replace")
