# Support for reading acceleration data from RFID sensor
#
# Copyright (C) 2024 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import binascii, hashlib, json, logging, os, re, time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Any, Type

from ...bus import MCU_SPI_from_config


class BlockReadingError(Exception):
    def __init__(self, msg):
        super().__init__(msg)
        self.msg = msg

    def __str__(self):
        return f"BlockReadingError: {self.msg}"


@dataclass(frozen=True)
class MFRC522Register:
    """
    Register address of MFRC522
    Described in chapter 9 of the datasheet.
    """
    # Page 0: Command and status
    # starts and stops command execution
    REG_COMMAND: int = 0x01
    # enable and disable interrupt request control bits
    REG_COM_IEN: int = 0x02
    # enable and disable interrupt request control bits
    REG_DIV_IEN: int = 0x03
    # interrupt request bits
    REG_COM_IRQ: int = 0x04
    # interrupt request bits
    REG_DIV_IRQ: int = 0x05
    # error bits showing the error status of the last command executed
    REG_ERROR: int = 0x06
    # communication status bits
    REG_STATUS_1: int = 0x07
    # receiver and transmitter status bits
    REG_STATUS_2: int = 0x08
    # input and output of 64 byte FIFO buffer
    REG_FIFO_DATA: int = 0x09

    # number of bytes stored in the FIFO buffer
    REG_FIFO_LEVEL: int = 0x0A
    # level for FIFO underflow and overflow warning
    REG_WATER_LEVEL: int = 0x0B
    # miscellaneous control registers
    REG_CONTROL: int = 0x0C
    # adjustments for bit-oriented frames
    REG_BIT_FRAMING: int = 0x0D
    # bit position of the first bit-collision detected on the RF interface
    REG_COLL: int = 0x0E

    # Page 1: Command
    # defines general modes for transmitting and receiving
    REG_MODE: int = 0x11
    # defines transmission data rate and framing
    REG_TX_MODE: int = 0x12
    # defines reception data rate and framing
    REG_RX_MODE: int = 0x13
    # controls the logical behavior of the antenna driver pins TX1 and TX2
    REG_TX_CONTROL: int = 0x14
    # controls the setting of the transmission modulation
    REG_TX_ASK: int = 0x15
    # selects the internal sources for the antenna driver
    REG_TX_SEL: int = 0x16
    # selects internal receiver settings
    REG_RX_SEL: int = 0x17
    # selects thresholds for the bit decoder
    REG_RX_THRESHOLD: int = 0x18
    # defines demodulator settings
    REG_DEMOD: int = 0x19

    # controls some MIFARE communication transmit parameters
    REG_MF_TX: int = 0x1C
    # controls some MIFARE communication receive parameters
    REG_MF_RX: int = 0x1D
    # selects the speed of the serial UART interface
    REG_SERIAL_SPEED: int = 0x1F

    # Page 2: Configuration
    # shows the MSB and LSB values of the CRC calculation
    REG_CRC_RESULT_H: int = 0x21
    REG_CRC_RESULT_L: int = 0x22
    # controls the ModWidth setting?
    REG_MOD_WIDTH: int = 0x24
    # configures the receiver gain
    REG_RF_CFG: int = 0x26
    # selects the conductance of the antenna driver pins
    # TX1 and TX2 for modulation
    REG_GS_N: int = 0x27
    # defines the conductance of the p-driver output during
    # periods of no modulation
    REG_CW_GS_P: int = 0x28
    # defines the conductance of the p-driver output during
    # periods of modulation
    REG_MOD_GS_P: int = 0x29

    # defines settings for the internal timer
    REG_T_MODE: int = 0x2A
    # the lower 8 bits of the TPrescaler value. The 4 high bits are in TModeReg.
    REG_T_PRESCALER: int = 0x2B
    # defines the 16-bit timer reload value
    REG_T_RELOAD_H: int = 0x2C
    REG_T_RELOAD_L: int = 0x2D
    REG_TCOUNTER_VAL_H: int = 0x2E
    REG_TCOUNTER_VAL_L: int = 0x2F

    # Page 3: Test register
    # general test signal configuration
    REG_TEST_SEL_1: int = 0x31
    # general test signal configuration
    REG_TEST_SEL_2: int = 0x32
    # enables pin output driver on pins D1 to D7
    REG_TEST_PIN_EN: int = 0x33
    # defines the values for D1 to D7 when it is used as an I/O bus
    REG_TEST_PIN_VALUE: int = 0x34
    # shows the status of the internal test bus
    REG_TEST_BUS: int = 0x35
    # controls the digital self-test
    REG_AUTO_TEST: int = 0x36
    # shows the software version
    REG_VERSION: int = 0x37
    # controls the pins AUX1 and AUX2
    REG_ANALOG_TEST: int = 0x38
    # defines the test value for TestDAC1
    REG_TEST_DAC_1: int = 0x39
    # defines the test value for TestDAC2
    REG_TEST_DAC_2: int = 0x3A
    # shows the value of ADC I and Q channels
    REG_TEST_ADC: int = 0x3B


    REG_TX_AUTO: int = 0x2C



@dataclass(frozen=True)
class MFRC522Config:
    """
    Values enumerate of MFRC522

    PCD_*:
        MFRC522 commands.
        Described in chapter 10 of the datasheet.

    PICC_*:
        Commands sent to the PICC.
    """
    # PCD Commands
    # Described in chapter 10 of the datasheet.

    # No action, cancels current command execution
    PCD_IDLE: int = 0x00
    # Stores 25 bytes into the internal buffer
    PCD_MEM: int = 0x01
    # Generates a 10-byte random ID number
    PCD_Generate_Random_ID: int = 0x02
    # Activates the CRC coprocessor or performs a self-test
    PCD_CALC_CRC: int = 0x03
    # Transmits data from the FIFO buffer
    PCD_TRANSMIT: int = 0x04
    # No command change, can be used to modify
    # the CommandReg register bits without affecting the command,
    # for example, the PowerDown bit
    PCD_NO_CMD_CHANGE: int = 0x07
    # Activates the receiver circuits
    PCD_RECEIVE: int = 0x08
    # Transmits data from FIFO buffer to antenna
    # and automatically activates the receiver after transmission
    PCD_TRANSCEIVE: int = 0x0C
    # Performs the MIFARE standard authentication as a reader
    PCD_AUTHENT: int = 0x0E
    # Resets the MFRC522
    PCD_SOFT_RESET: int = 0x0F

    # Commands send to PICC
    # The commands used by the PCD to manage
    # communication with several PICCs

    # REQuest command, Type A.
    # Invites PICCs in state IDLE to go to READY
    # and prepare for anticollision or selection.
    PICC_CMD_REQA: int = 0x26
    # Reads one 16 byte block from the authenticated sector of the PICC.
    PICC_CMD_READ: int = 0x30
    # Perform authentication with Key A
    PICC_CMD_AUTH_KEY_A: int = 0x60
    # Anti collision/Select, Cascade Level 1
    PICC_CMD_SEL_CL1: int = 0x93
    # Writes one 16 byte block to the authenticated sector of the PICC.
    PICC_CMD_WRITE: int = 0xA0

    PICC_CMD_HALT: int = 0x50

    # Interrupt request

    # Wait for interrupt request flag
    IRQ_WAIT: int = 0x30
    # Interrupt request enable flag
    IRQ_EN: int = 0x77
    # Interrupt request for AUTH
    IRQ_WAIT_AUTH: int = 0x10
    IRQ_EN_AUTH: int = 0x12

    # Mode
    # b'10000000'
    MOD_HIGH = 0x80
    # b'01111111'
    MOD_LOW = 0x7F

    # Error
    COLL_ERR = 0x08
    PARITY_ERR = 0x04
    PROTOCOL_ERR = 0x01

    # CRC
    CRC_ENABLE = 0x03


@dataclass(frozen=True)
class MFRC522Status:
    """
    Status string defined for MFRC522.
    """
    OK: str = "OK"
    ERROR: str = "ERROR"
    # PCD can't find available tag -> no tag error
    NO_TAG: str = "NO_TAG_ERROR"
    # Timeout in communication
    TIMEOUT: str = "TIMEOUT"
    # Collission detected
    COLLISION: str = "COLLISION"


@dataclass
class RFIDDict:
    """
    Data class for RFID data,
    defining field mappings and handles data storage.
    """
    # MIFARE Raw
    uid: str = field(init=False)
    tag_manufacturer: str = field(init=False)
    tag_version: int = field(init=False)
    # Filament
    filament_manufacturer: str = field(init=False)
    manufacture_datetime: str = field(init=False)
    filament_material_type: str = field(init=False)
    filament_type_detailed: str = field(init=False)
    serial_number: str = field(init=False)
    color_code: str = field(init=False)
    color_name_a: str = field(init=False)
    color_name_b: str = field(init=False)
    filament_diameter: int = field(init=False)
    density: int = field(init=False)
    shrinkage: int = field(init=False)
    flow_ratio: int = field(init=False)
    max_volumetric_speed: int = field(init=False)
    minimal_purge_on_wipe_tower: int = field(init=False)
    # Spool
    spool_material_type: str = field(init=False)
    spool_weight: int = field(init=False)
    spool_empty_weight: int = field(init=False)
    spool_outer_diameter: int = field(init=False)
    spool_inner_diameter: int = field(init=False)
    spool_width: int = field(init=False)
    # Temperature
    drying_time: int = field(init=False)
    drying_temperature_min: int = field(init=False)
    drying_temperature_max: int = field(init=False)
    bed_temerature_min: int = field(init=False)
    bed_temerature_max: int = field(init=False)
    printing_temperature_min: int = field(init=False)
    printing_temperature_max: int = field(init=False)
    softening_temperature: int = field(init=False)
    bed_temperature: int = field(init=False)
    # Printer
    print_speed_min: int = field(init=False)
    print_speed_max: int = field(init=False)
    nozzle_diameter: int = field(init=False)

    _fields = {
        # block 0
        "uid": (0, 0, 8),  # Read substring from block "0", offset 0, length 8
        "tag_manufacturer": (0, 8, 24),
        # block 1
        "tag_version": (1, 0, 4),
        "filament_manufacturer": (1, 4, 28),
        # block 2
        "manufacture_datetime": (2, 0, 32),
        #### block 3 MIFARE encryption keys ####
        # block 4
        "filament_material_type": (4, 0, 32),
        # block 5
        "filament_type_detailed": (5, 0, 32),
        # block 6
        "serial_number": (6, 0, 32),
        #### block 7 MIFARE encryption keys ####
        # block 8
        "color_code": (8, 0, 6),
        "color_name_a": (8, 6, 26),
        # block 9
        "color_name_b": (9, 0, 32),
        # block 10
        "filament_diameter": (10, 0, 4),
        "density": (10, 4, 4),
        "shrinkage": (10, 8, 4),
        "flow_ratio": (10, 12, 4),
        "max_volumetric_speed": (10, 16, 4),
        "minimal_purge_on_wipe_tower": (10, 20, 4),
        #### block 11 MIFARE encryption keys ####
        # block 12
        # block 13
        # block 14
        #### block 15 MIFARE encryption keys ####
        # block 16
        "spool_material_type": (16, 0, 32),
        # block 17
        "spool_weight": (17, 0, 4),
        "spool_empty_weight": (17, 4, 4),
        "spool_outer_diameter": (17, 8, 4),
        "spool_inner_diameter": (17, 12, 4),
        "spool_width": (17, 16, 4),
        # block 18
        "drying_time": (18, 0, 4),
        "drying_temperature_min": (18, 4, 4),
        "drying_temperature_max": (18, 8, 4),
        "bed_temerature_min": (18, 12, 4),
        "bed_temerature_max": (18, 16, 4),
        "printing_temperature_min": (18, 20, 4),
        "printing_temperature_max": (18, 24, 4),
        "softening_temperature": (18, 28, 4),
        #### block 19 MIFARE encryption keys ####
        # block 20
        "bed_temperature": (20, 0, 4),
        # block 21
        # block 22
        #### block 23 MIFARE encryption keys ####
        # block 24
        "print_speed_min": (24, 0, 4),
        "print_speed_max": (24, 4, 4),
        "nozzle_diameter": (24, 8, 4),
    }

    # block '0' should not zfill
    # [0] + list(range(3, 64, 4))
    assemble_pass_blocks = [0, 3, 7, 11, 15, 19, 23, 27, 31,
        35, 39, 43, 47, 51, 55, 59, 63]

    encode_pass_fields = ["color_code"]

    @classmethod
    def get_field_keys(cls):
        """
        Get all field keys.
        """
        return cls._fields.keys()

    @classmethod
    def get_field_type(cls, field_name):
        return cls.__annotations__.get(field_name, None)

    @classmethod
    def get_assemble_pass_blocks(cls):
        return cls.assemble_pass_blocks

    @classmethod
    def get_encode_pass_fields(cls):
        return cls.encode_pass_fields

    @classmethod
    def get_field_items(cls):
        """
        Get list of fields that need to be passed through encoding.
        """
        return cls._fields.items()

    @classmethod
    def get_field_length(cls, field_name):
        # _, _, expected_length = field_info
        return cls._fields.get(field_name, (None, None, None))[2]

    @classmethod
    def get_block_fields(cls, block_num):
        # Filter fields belonging to the specified block
        # block_fields: [(field, offset), ...]
        block_fields = [
            (field, info[1]) for field, info in cls._fields.items()
            if info[0] == block_num
        ]
        # Sort fields by offset
        return sorted(block_fields, key=lambda tup: tup[1])


# Models for MFRC522
class BaseModel:
    """
    Base model class providing common functionalities.
    Design like a tiny ORM.
    """
    def __init__(self, dct_obj=None):
        self.dct_obj = dct_obj or self.__class__()

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert model to a dictionary representation.
        """
        return {key: value
            for key, value in self.dct_obj.__dict__.items()
            if value is not None}

    def to_json(self) -> str:
        """
        Convert model to JSON representation.
        """
        return json.dumps(self.to_dict(), indent=4)

    def from_dict(self, data: Dict[str, Any]):
        """
        Setup a model instance from a dictionary.
        """
        for key, value in data.items():
            setattr(self.dct_obj, key, value)
        return self.dct_obj

    def from_json(self, json_data: str):
        """
        Setup a model instance from JSON data.
        """
        return self.from_dict(json.loads(json_data))


class RFIDModel(BaseModel):
    """
    Model for decoding RFID data.
    Like an ORM model.
    """
    def __init__(self):
        super().__init__(RFIDDict())
        self.skip_fields = ("color_code")

    # For Block Reading
    def decode_hex_to_string(self, hex_string):
        try:
            return bytes.fromhex(hex_string).decode("utf-8").rstrip("\x00")
        except Exception as e:
            logging.error(
                f"Error decoding string with hex: {hex_string}."
                f" Exception: {e}")
            return hex_string

    def decode_hex_to_int(self, hex_string):
        try:
            unhex = binascii.unhexlify(hex_string)
            return int.from_bytes(unhex, byteorder="little")
        except Exception as e:
            logging.error(
                f"Error decoding int with hex: {hex_string}. Exception: {e}")
            # return -1
            return hex_string

    def decode_field(self, field_name, hex_string):
        """
        Decode the field based on its type.
        """
        if field_name in self.skip_fields:
            return hex_string

        # Get the field type from the class annotations
        field_type = RFIDDict.get_field_type(field_name)
        if not field_type:
            raise ValueError(f"Unknown field {field_name}")

        if field_type == str:
            return self.decode_hex_to_string(hex_string)
        elif field_type == int:
            return self.decode_hex_to_int(hex_string)
        else:
            raise ValueError(
                f"Unsupported type {field_type} for field {field_name}")

    def from_blocks(self, blocks):
        """
        Create a new instance from blocks of data.
        """
        data = {}

        for field, (block_num, offset, length) in RFIDDict.get_field_items():
            block_value = blocks.get(str(block_num), "").replace(" ", "")

            if len(block_value) < offset + length:
                raise ValueError(
                    f"Block {block_num} is too short for field {field}")

            hex_segment = block_value[offset:offset + length]

            if block_num == 0:
                # If block 0, don't decode
                data[field] = hex_segment
            else:
                # Dynamically call the decode method
                data[field] = self.decode_field(field, hex_segment)

        # Return RFIDDict instance using the from_dict method
        return self.from_dict(data)

    # For Block Writing
    def encode_string_to_hex(self, string):
        try:
            utf8_string = string.encode("utf-8")
            return binascii.hexlify(utf8_string).decode("utf-8").upper()
        except Exception as e:
            logging.error(
                f"Error encoding string to hex: {string}. Exception: {e}")
            return string

    def encode_int_to_hex(self, integer):
        try:
            byte_length = (integer.bit_length() + 7) // 8
            byte_data = integer.to_bytes(byte_length, byteorder="little")
            hex_string = binascii.hexlify(byte_data).decode("utf-8").upper()
            return hex_string
        except Exception as e:
            logging.error(
                f"Error encoding int to hex: {integer}. Exception: {e}")
            return integer

    def encode_field(self, field_name, value):
        """
        Encode the field based on its type.
        """
        # Get the field type from the class annotations
        field_type = RFIDDict.get_field_type(field_name)
        if not field_type:
            raise ValueError(f"Unknown field {field_name}")

        if field_name in RFIDDict.get_encode_pass_fields():
            return value
        elif field_type == str:
            return self.encode_string_to_hex(value)
        elif field_type == int:
            return self.encode_int_to_hex(value)
        else:
            raise ValueError(
                f"Unsupported type {field_type} for field {field_name}")

    def pad_field(self, field_name, value):
        """
        Pad the field value with zeros to match the expected length.
        """
        # Get the expected length from RFIDDict._fields
        expected_length = RFIDDict.get_field_length(field_name)
        if not expected_length:
            raise ValueError(f"Unknown field {field_name}")

        if len(value) < expected_length:
            # Pad the value with zeros
            value = value.ljust(expected_length, '0')

        # return value[:expected_length]  # Truncate if needed
        return value

    def assemble_block_data(self, block_num):
        """
        Assemble all fields of a specific block into a single data string.
        """
        if block_num in RFIDDict.get_assemble_pass_blocks():
            return ""

        sorted_fields = RFIDDict.get_block_fields(block_num)

        # Initialize an empty data string for the block
        block_data = ""

        for field, _ in sorted_fields:
            value = getattr(self.dct_obj, field, None)
            if not value:
                continue

            # Dynamically call the decode method
            val_enc = self.encode_field(field, value)
            # Pad the field value
            val_pad = self.pad_field(field, val_enc)
            # Insert the encoded value at the correct offset
            block_data += val_pad

        return block_data

    def prepare_blocks_writing(self):
        """
        Prepare all blocks of data for writing, assembling each block.
        """
        blocks = {}
        block_nums = {info[0] for _,info in RFIDDict.get_field_items()}

        block_data_len = 32
        block_data_stash = ""

        for block_num in block_nums:
            block_data = self.assemble_block_data(block_num)

            if not block_data and block_data_stash:
                block_data = block_data_stash

            # Empty stash anyway, don't delay to another block
            if block_data_stash:
                block_data_stash = ""

            if block_data:
                if len(block_data) > block_data_len:
                    # If block data overlimit, stash to next block
                    block_data_stash = block_data[block_data_len:]
                    block_data = block_data[:block_data_len]

                elif len(block_data) < block_data_len:
                    # If block data not have enough 0, zfill
                    block_data = block_data.ljust(block_data_len, '0')

                # Convert hex string to bytes list of integers
                blocks[block_num] = list(bytearray.fromhex(block_data))

        return blocks


class RFIDCache:
    """
    Simple Cache for RFID.
    If need to use in multiple threading,
    to add lock for threading safe.

    No expired time set.
    If need to build a LRU cache, use deque
    """
    def __init__(self, max_size=16):
        self._cache = OrderedDict()
        self.max_size = max_size

    def get(self, key):
        if key in self._cache:
            # Move to the end to indicate recent use
            val = self._cache.pop(key)
            self._cache[key] = val
            return val
        return None

    def add(self, key, val):
        if key in self._cache:
            # Remove old val with same key
            self._cache.pop(key)
        elif len(self._cache) >= self.max_size:
            # Check size, if out of size, remove oldest val
            self._cache.popitem(last=False)

        self._cache[key] = val

    def get_cache(self):
        return self._cache

    def gen_key(self, s, prefix=None):
        return f"{prefix}:{s}" if prefix else s


class HashAssistant:
    def __init__(self, algorithm=hashlib.sha256):
        """
        Initialize the HashAssistant with
        a specific hash algorithm.
        Defaults to SHA-256.
        """
        self.algorithm = algorithm

    def _hash_data(self, string):
        """
        Compute the hash of the given hexadecimal string.
        """
        hasher = self.algorithm()
        hasher.update(bytes.fromhex(string))
        # 32 bytes binary int data
        return hasher.digest()

    def hash_as_list(self, string):
        """
        Get the hash of the given hexadecimal string
        as a list of integers.
        """
        hash_data = self._hash_data(string)
        return list(hash_data)

    def hash_as_string(self, string):
        """
        Get the hash of the given hexadecimal string
        as an uppercase hex string.
        """
        hash_data = self._hash_data(string)
        return hash_data.hex().upper()

    def block_to_string(self, block_data_lst):
        """
        Convert a list of block data to a continuous hexadecimal string.

        - block_data_lst: List of tuples where each tuple contains
          (block_num, block_data),
          where block_data is a space-separated hex string.

        [(block_num, block_data), ..]
        block_data => "23 C0 20 F7 34 08 04 ..."
        transform to => "23C020F7340804..."
        """
        # Sort by block_num ascend
        block_data_lst.sort(key=lambda tup:tup[0])

        # Concatenate all block data strings without spaces
        return ''.join(map(lambda tup:tup[1].replace(" ", ""), block_data_lst))

    def is_valid_length(self, hash_string):
        """
        Check if the length of the given hash string is valid for SHA-256.
        SHA-256 gen 32 bytes hex data, len == 32 * 2
        """
        return len(hash_string) == 64

    def is_hexadecimal(self, hash_string):
        """
        Check if the given string is a valid hexadecimal string.
        """
        return bool(re.fullmatch(r'[0-9a-fA-F]*', hash_string))

    def has_high_zero_ratio(self, hash_string, threshold=0.6):
        """
        Determine if the hash string has a high ratio of zeroes.

        threshold
            The ratio of zeroes to
            total characters that defines "high".
        """
        if not self.is_valid_length(hash_string):
            # Invalid length, return True as pass failed
            return True

        zero_count = hash_string.count('0')
        zero_ratio = zero_count / len(hash_string)

        return zero_ratio >= threshold


class MFRC522Handler:
    """
    The MFRC522 is a highly integrated reader/writer
    IC for contactless communication at 13.56 MHz.

    The following host interfaces are provided:
    • Serial Peripheral Interface (SPI)
    • Serial UART
    • I2C-bus interface
    """
    def __init__(self, spi):
        self.spi = spi
        self.auth_key = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]

        self.retry_times = 10
        self.timeout_crc = 0.5 # seconds
        self.timeout_cmd_exec = 0.5 # seconds

        # Use register address "REG*" as string
        # -> getattr(self.reg, "REG*")
        self.reg = MFRC522Register()

        # Use config data as attribute
        # -> self.config.MODE_HIGH
        self.config = MFRC522Config()

        # Use status as attribute
        # -> self.status.OK
        self.status = MFRC522Status()

        # Soft reset first.
        self.pcd_reset()

        # When communicating with a PICC we
        # need a timeout if something goes wrong.
        # Set the timer mode and prescaler

        # TAuto=1
        # Timer starts automatically at the end
        # of the transmission in all communication modes at all speeds
        self.write_reg("REG_T_MODE", 0x80)

        # TPreScaler = TModeReg[3..0]:TPrescalerReg,
        # ie 0xA9=169 => f_timer=40kHz, ie a timer period of 25μs.
        # self.write_reg("REG_T_PRESCALER", 0xA9)
        self.write_reg("REG_T_PRESCALER", 0x3E)

        # Reload timer with 0x03E8 = 1000, ie 25ms before timeout.
        self.write_reg("REG_T_RELOAD_H", 0x03)
        self.write_reg("REG_T_RELOAD_L", 0xE8)

        # Enable the auto-timer for transmission and set the mode

        # Default 0x00.
        # Force a 100% ASK modulation independent
        # of the ModGsPReg register setting
        self.write_reg("REG_TX_ASK", 0x40)

        # Default 0x3F.
        # Set the preset value for the CRC coprocessor
        # for the CalcCRC command to 0x6363
        self.write_reg("REG_MODE", 0x3D)

        # Enhance RFID Reading distance, testing
        # Internal reception settings
        # self.write_reg("REG_RX_SEL", 0x86)
        # Reception gain
        # self.write_reg("REG_RX_THRESHOLD", 0x62)
        # # self.write_reg("REG_RF_CFG", 0x7F)
        # self.write_reg("REG_RF_CFG", 0x74)
        # self.write_reg("REG_GS_N", 0xF8)
        # self.write_reg("REG_CW_GS_P", 0x3f)
        self.write_reg("REG_CW_GS_P", 0x1C)

        # Turn on the antenna
        # self.pcd_antenna_on()

    # Basic
    def get_reg_addr(self, reg_name):
        reg_addr = getattr(self.reg, reg_name)
        # When using SPI with MFRC522, all addresses are shifted
        # one bit left in the "SPI address byte".
        reg_addr = reg_addr << 1

        return reg_addr

    def get_config_value(self, val):
        # If value is a string, get the 16bit int from MFRC522Config()
        val_data = val if type(val) == int else getattr(self.config, val)
        return val_data

    def write_reg(self, reg_name, val):
        """
        Write value into register of MFRC522.
        """
        reg_addr = self.get_reg_addr(reg_name)
        val_data = self.get_config_value(val)

        # In klippy, spi.spi_send() is similar to "spi_transfer",
        # but it does not generate a "spi_transfer_response" message.
        self.spi.spi_send([reg_addr, val_data])

    def bulk_write_reg(self, reg_name, val_lst):
        """
        Bulk write value into register of MFRC522.
        """
        if not val_lst:
            return

        reg_addr = self.get_reg_addr(reg_name)
        send_lst = [reg_addr, ]

        for val in val_lst:
            # If value is a string, get the 16bit int from MFRC522Config()
            val_data = self.get_config_value(val)
            send_lst.append(val_data)

        # In klippy, spi.spi_send() is similar to "spi_transfer",
        # but it does not generate a "spi_transfer_response" message.
        self.spi.spi_send(send_lst)

    def read_reg(self, reg_name):
        """
        Read value from register of MFRC522.
        """
        reg_addr = self.get_reg_addr(reg_name)

        # In klippy, 'spi_transfer oid=%c data=%*s' causes the
        # micro-controller to send 'data' to the spi device specified by 'oid'
        # and it generates a "spi_transfer_response" response message with the
        # data returned during the transmission.
        # The spi.spi_transfer(data) setup oid and bind
        # spi_transfer_response itself.
        ret = self.spi.spi_transfer([self.config.MOD_HIGH | reg_addr, 0x00])
        # logging.info(f"ret: {ret}")

        # The ret["response"] is binary data like b"\x00\xb2".
        # Need the b"\xb2" only.
        res = bytearray(ret["response"])[1]

        return res

    def bulk_read_reg(self, reg_name, count):
        """
        Bulk read value from same register of MFRC522 "count" times.
        """
        if count <= 0:
            return None

        reg_addr = self.get_reg_addr(reg_name)
        reg_lst = [self.config.MOD_HIGH | reg_addr] * count
        reg_lst.append(0x00)

        ret = self.spi.spi_transfer(reg_lst)
        # logging.info(f"ret: {ret}")

        # result is a list without ret["response"][0]
        res = bytearray(ret["response"])[1:]
        return res

    # PCD Related
    def pcd_reset(self):
        """
        Reset MFRC522 by writing the PCD_SOFT_RESET
        command to the CommandReg register,
        which resets its internal state and clears all registers.

        After the reset, the chip is ready to accept new commands.
        """
        self.write_reg("REG_COMMAND", self.config.PCD_SOFT_RESET)

    def pcd_set_bit_mask(self, reg, mask):
        """
        Sets specific bits in a register of MFRC522
        by performing a bitwise OR operation with the provided mask.
        """
        # Read the current value of the register
        val = self.read_reg(reg)
        # Set the desired bits using a bitwise OR operation
        # mask = getattr(self.config, mask) if type(mask) == str else mask
        self.write_reg(reg, val | mask)

    def pcd_clear_bit_mask(self, reg, mask):
        """
        Clear specific bits in a register of MFRC522
        by performing a bitwise OR operation with the provided mask.
        """
        # Read the current value of the register
        val = self.read_reg(reg)
        # Clear the desired bits using a bitwise AND operation
        # mask = getattr(self.config, mask) if type(mask) == str else mask
        self.write_reg(reg, val & (~mask))

    def pcd_antenna_on(self):
        """
        Turns on the antenna of MFRC522 by
        setting the TxControlReg register.
        Do nothing if the antenna is already on.
        """
        # Read the current value of the TxControlReg register
        val = self.read_reg("REG_TX_CONTROL")
        # Check if the least significant two bits are already set
        if (val & 0x03) != 0x03:
            # If not, turn on the antenna by setting the bits using a bit mask
            self.pcd_set_bit_mask("REG_TX_CONTROL", 0x03)

    def pcd_antenna_off(self):
        """
        Turns off the antenna of MFRC522 by
        clearing the TxControlReg register.
        """
        # Clear the least significant two bits of
        # the TxControlReg register to turn off the antenna
        self.pcd_clear_bit_mask("REG_TX_CONTROL", 0x03)

    @contextmanager
    def antenna_manager(self):
        self.pcd_antenna_on()
        try:
            yield
        finally:
            self.pcd_antenna_off()

    def pcd_get_version(self):
        """
        Get Firmware Version
        """
        return self.read_reg("REG_VERSION")

    def pcd_calculate_CRC(self, buffer):
        """
        Calculates the CRC value for the given input data using the MFRC522
        chip. "buffer" should be an int list like [0x00, 0x01, ...]
        """
        # Clear the CRC IRQ flag and set the FIFO level to maximum.
        self.pcd_clear_bit_mask("REG_DIV_IRQ", 0x04)
        self.pcd_set_bit_mask("REG_FIFO_LEVEL", self.config.MOD_HIGH)

        # Write the input data to the FIFO.
        self.bulk_write_reg("REG_FIFO_DATA", buffer)

        # Start the CRC calculation command.
        self.write_reg("REG_COMMAND", self.config.PCD_CALC_CRC)

        # Wait for the CRC calculation to complete.
        start_time = time.time()
        while 1:
            val = self.read_reg("REG_DIV_IRQ")
            if (val & 0x04):
                # CRCIRq bit set - calculation done

                # Stop calculating CRC for new content in the FIFO.
                # self.write_reg("REG_COMMAND", self.config.PCD_IDLE)
                break

            if time.time() - start_time > self.timeout_crc:
                # Time out
                break

        # Read the calculated CRC value from the chip.
        return [self.read_reg("REG_CRC_RESULT_L"),
            self.read_reg("REG_CRC_RESULT_H")]

    def pcd_stop_crypto_1(self):
        """
        Stops the authentication process.
        Clear MFCrypto1On bit.
        """
        # Status2Reg[7..0] bits are:
        # TempSensClear I2CForceHS reserved reserved MFCrypto1On ModemState[2:0]
        self.pcd_clear_bit_mask("REG_STATUS_2", 0x08)

    def pcd_authenticate(self, auth_mode, block_num, auth_key, uid):
        """
        Authenticates a tag or card for a specific block.

        Auth a whole sector, not only a single block
        """
        assert uid, f"AUTH get error UID: {uid}"

        # First byte should be the auth_mode
        # Second byte is the block_num
        buffer = [auth_mode, block_num]

        # Append the authKey which default is 6 bytes of 0xFF
        buffer += auth_key
        # Next append the first 4 bytes of the UID
        buffer += uid[:4]

        try:
            # Start the authentication
            status, _, _ = self.pcd_to_picc(
                command=self.config.PCD_AUTHENT, send_data=buffer)

        except Exception as e:
            logging.error(f"AUTH error: {e}")
            return self.status.ERROR

        # Check if an error occurred
        if status != self.status.OK:
            logging.error(f"AUTH error, status: {status}")

        # if not (self.read_reg("REG_STATUS_2") & 0x08) != 0:
        #     logging.info("AUTH ERROR(status2reg & 0x08) != 0")

        return status

    # PICC Related
    def picc_select(self, uid):
        """
        Selects a tag or card for communication.
        Which also means transmits SELECT/ANTICOLLISION
        commands to select a single PICC.

        Before calling this function the PICCs must be
        placed in the READY(*) state by calling
        self.request() or self.wakeup().

        On success:
        - The chosen PICC is in state ACTIVE(*) and
        all other PICCs have returned to state IDLE/HALT.
        - The UID size and value of the chosen PICC
        is returned in *uid along with the SAK.

        Argument "uid" should be an int list.
        """
        assert uid, f"Select get error UID: {uid}"

        buffer = []

        # Add the command byte and tag type to the buffer
        buffer.append(self.config.PICC_CMD_SEL_CL1)
        # Number of Valid Bits: Seven whole bytes
        buffer.append(0x70)
        # Add the serial number of the tag to the buffer
        buffer += uid[:5]

        try:
            # Calculate the CRC values for the buffer
            buffer_crc = self.pcd_calculate_CRC(buffer)
            buffer += buffer_crc[:2]

            # Send the buffer to the tag and receive the response
            status, back_data, back_bits = self.pcd_to_picc(
                command=self.config.PCD_TRANSCEIVE,
                send_data=buffer, need_bits_len=True)

        except Exception as e:
            logging.error(f"Select error: {e}")
            return self.status.ERROR

        # Check if the response is successful
        # and has the expected length 0x18 == 24
        # Return error if the response is not successful
        # or has an unexpected length
        if (status != self.status.OK) or (back_bits != 0x18):
            return self.status.ERROR

        # Return the first byte of the response, which is the size
        # return back_data[0]
        return self.status.OK

    # Communicate between PCD and PICC
    def pcd_to_picc(self, command, send_data, need_bits_len=False):
        """
        Transfers data to the MFRC522 FIFO,
        executes a command,
        waits for completion
        and transfers data back from the FIFO.
        """
        # Set interrupt request and wait flags based on command
        if command == self.config.PCD_AUTHENT:
            irq_wait = self.config.IRQ_WAIT_AUTH
            irq_enable = self.config.IRQ_EN_AUTH
        else:
            # Default for self.config.PCD_TRANSCEIVE
            irq_wait = self.config.IRQ_WAIT
            irq_enable = self.config.IRQ_EN

        # Enable interrupts and reset FIFO buffer
        self.write_reg("REG_COM_IEN", irq_enable | self.config.MOD_HIGH)
        self.pcd_clear_bit_mask("REG_COM_IRQ", self.config.MOD_HIGH)
        self.pcd_set_bit_mask("REG_FIFO_LEVEL", self.config.MOD_HIGH)

        # Put MFRC522 into idle state
        self.write_reg("REG_COMMAND", self.config.PCD_IDLE)

        # Write data to FIFO buffer
        self.bulk_write_reg("REG_FIFO_DATA", send_data)

        # Auto CRC
        # if command in (self.config.PCD_TRANSCEIVE, self.config.PCD_AUTHENT):
        #     self.pcd_set_bit_mask("REG_TX_AUTO", 0x03)

        # Start command execution
        self.write_reg("REG_COMMAND", command)

        # Set bit framing if command is transceive
        if command == self.config.PCD_TRANSCEIVE:
            self.pcd_set_bit_mask("REG_BIT_FRAMING", self.config.MOD_HIGH)

        # Wait for command execution
        # start_time = time.time()
        # while 1:
        #     # time.sleep(0.01)
        #     val = self.read_reg("REG_COM_IRQ")
        #     # Break if interrupt request received or timeout
        #     if (val & 0x01) or (val & irq_wait):
        #         break
        #     if time.time() - start_time > self.timeout_cmd_exec:
        #         # Time out
        #         logging.error("Time out while reading REG_COM_IRQ")
        #         # status = self.status.TIMEOUT
        #         # Return? Raise TimeoutException?
        #         break
        start_time = time.time()
        timeout = (self.timeout_cmd_exec
                   * (2 if command == self.config.PCD_AUTHENT else 1))
        while time.time() - start_time < timeout:
            val = self.read_reg("REG_COM_IRQ")
            if val & (0x01 | irq_wait):
                break
            time.sleep(0.005)
        else:
            # Time out
            logging.error("Command/Reading REG_COM_IRQ timeout")
            return self.status.TIMEOUT, [], 0

        # Clear bit framing if command is transceive
        self.pcd_clear_bit_mask("REG_BIT_FRAMING", self.config.MOD_HIGH)

        status = self.status.ERROR
        back_data = []
        back_bits = 0

        # Check for errors and update status accordingly
        val_err = self.read_reg("REG_ERROR")
        if (val_err & 0x1B) == 0x00:
            if val & irq_enable & 0x01:
                status = self.status.NO_TAG
            else:
                status = self.status.OK

            # Read response data if command is transceive
            if command == self.config.PCD_TRANSCEIVE:
                lvl = self.read_reg("REG_FIFO_LEVEL") or 1

                # Read only the first 16 bytes of FIFO data
                # Some unknown bytes may come after 16+
                max_len = 16
                lvl = min(lvl, max_len)

                back_data += self.bulk_read_reg("REG_FIFO_DATA", lvl)
                if need_bits_len:
                    last_bits = self.read_reg("REG_CONTROL") & 0x07

                    if last_bits != 0:
                        back_bits = (lvl - 1) * 8 + last_bits
                    else:
                        back_bits = lvl * 8

        return status, back_data, back_bits

    def request(self, mode):
        """
        Transmits a request command to a tag or card to initiate communication.
        Type A.
        Invites PICCs in state IDLE to go to READY
        and prepare for anticollision or selection.
        """
        # Append the request mode to the buffer
        buffer = [mode,]

        # Set the bit framing register to 0x07
        self.write_reg("REG_BIT_FRAMING", 0x07)

        try:
            # Send the request to the card
            status, _, back_bits = self.pcd_to_picc(
                command=self.config.PCD_TRANSCEIVE,
                send_data=buffer, need_bits_len=True)

        except Exception as e:
            logging.error(f"Request error: {e}")
            return self.status.ERROR

        # If the status is not OK
        # or the back bits are not 0x10 == 16,
        # set status to ERROR
        if (status != self.status.OK) or (back_bits != 0x10):
            status = self.status.ERROR

        return status

    def anticollision(self):
        """
        Sends an anticollision command.
        Performs an anticollision algorithm to a tag or card
        to prevent multiple tags from responding.
        """
        # Append the PICC_ANTICOLL command and 0x20 to the buffer list
        buffer = [self.config.PICC_CMD_SEL_CL1, 0x20]

        # Set the BitFramingReg to 0x00
        self.write_reg("REG_BIT_FRAMING", self.config.PCD_IDLE)

        try:
            status, back_data, _ = self.pcd_to_picc(
                command=self.config.PCD_TRANSCEIVE, send_data=buffer)
            # logging.info(f"status={status}, back_data={back_data}")
        except Exception as e:
            logging.error(f"Anti-collision error: {e}")
            status = self.status.ERROR
            back_data = []

        # logging.info(f"status={status}, back_data={back_data}")

        # Check if the operation was successful
        if status == self.status.OK:
            # Check if the back_data has the expected length of 5 bytes
            if len(back_data) == 5:
                # Calculate the XOR checksum of the first 4 bytes
                # of the back_data
                check = 0
                for i in range(4):
                    check = check ^ back_data[i]

                # Check if the calculated checksum matches the 5th
                # byte of back_data
                if check != back_data[4]:
                    # If not, set the status to ERROR
                    status = self.status.ERROR
            else:
                # If back_data doesn't have 5 bytes, set the status to ERROR
                status = self.status.ERROR

        # The back_data is UID of Tag
        return status, back_data

    def picc_halt(self):
        buffer = [self.config.PICC_CMD_HALT, 0]
        self.write_reg("REG_BIT_FRAMING", 0x00)
        # self.pcd_to_picc(self.config.PCD_TRANSCEIVE, buffer)

        buffer_crc = self.pcd_calculate_CRC(buffer)
        # self.pcd_to_picc(self.config.PCD_TRANSCEIVE, buffer + crc)
        self.pcd_to_picc(self.config.PCD_TRANSCEIVE, buffer + buffer_crc[:2])

    def detect_multiple_tags(self):
        uids = []
        while True:
            status, uid = self.anticollision()
            logging.info(f"status={status}, uid={uid}")

            if status != self.status.OK:
                break
            if not any(u == uid for u in uids):
                uids.append(uid)
                self.picc_halt()

        return uids

    def read_block(self, block_num):
        """
        Reads data from a specific block of a RFID card.
        """
        # Check block is valid
        assert block_num in range(64), f"block {block_num} is out of range(64)"

        err_msg = None

        buffer = [self.config.PICC_CMD_READ, block_num]
        try:
            # Calculate the CRC checksum for the command and block address
            buffer_crc = self.pcd_calculate_CRC(buffer)
            # Append the calculated checksum to the command and
            # block address array
            buffer += buffer_crc[:2]

            # Send the command and block address array to the RFID card
            # and receive response
            status, back_data, _ = self.pcd_to_picc(
                command=self.config.PCD_TRANSCEIVE, send_data=buffer)

        except Exception as e:
            err_msg = f"read block error: {e}"
            logging.error(err_msg)
            raise BlockReadingError(err_msg)

        if status != self.status.OK:
            err_msg = f"Error reading block: {block_num}, status: {status}"
        elif len(back_data) != 16:
            err_msg = (f"Error reading block: {block_num}"
                       f", length: {len(back_data)}")

        if err_msg:
            logging.error(err_msg)
            raise BlockReadingError(err_msg)

        # If response data has length 16, return data
        return back_data

    def write_block(self, block_num, data):
        """
        Write data to a specified block address in the RFID tag.
        Argument "data" should be a list of 16 bytes data.
        """
        # Check block is valid
        assert block_num in range(64), \
            f"block {block_num} is out of range(64)"

        buffer = [self.config.PICC_CMD_WRITE, block_num]

        try:
            # Calculate the CRC checksum for the command and block address
            # Append the calculated checksum
            buffer += self.pcd_calculate_CRC(buffer)[:2]

            # Send the buffer to the tag and receive the response
            status, back_data, back_bits = self.pcd_to_picc(
                command=self.config.PCD_TRANSCEIVE,
                send_data=buffer, need_bits_len=True)

        except Exception as e:
            logging.error(f"write block step 1 error: {e}")
            return False

        # Check if the write operation was successful or not
        if (status != self.status.OK or back_bits != 4
            or (back_data[0] & 0x0F) != 0x0A):
            logging.error(f"Write block failed, status: {status}")
            return False

        # Begin writing
        # If the initial write operation was successful,
        # write the actual data to the tag
        buffer_w = data[:16]

        try:
            # Calculate the CRC checksum for the data to be written
            # Append the calculated checksum
            buffer_w += self.pcd_calculate_CRC(buffer_w)[:2]

            # Send the buffer to the tag and receive the response
            status, back_data, back_bits = self.pcd_to_picc(
                command=self.config.PCD_TRANSCEIVE,
                send_data=buffer_w, need_bits_len=True)

        except Exception as e:
            logging.error(f"write block step 2 error: {e}")
            return False

        # Check if the write operation was successful or not
        if (status != self.status.OK or back_bits != 4
            or (back_data[0] & 0x0F) != 0x0A):
            logging.error(f"Error while writing, status: {status}")
            return False

        if status == self.status.OK:
            logging.info(f"Data written to block: {block_num}")

        return True

    # Bussiness func
    def format_block_data(self, block_data):
        """
        Convert block data list to string.
        [0x00, 0x0a, ...] -> "00 0A ..."
        """
        # assert block_data, f"Unavailable block_data: {block_data}"
        if block_data is not None:
            # block_data is a 16 bytes list
            s = ' '.join(hex(i).upper()[2:].zfill(2) for i in block_data)
        else:
            # logging.warning(f"Unavailable block_data: {block_data}")
            # logging.warning("block_data is None")
            s = ""
        return s

    # @antenna_manager
    def get_version(self):
        """
        Get Firmware Version
        """
        return self.pcd_get_version()

    # @antenna_manager
    def read_uid(self):
        """
        Attempt to read the tag ID from the RFID tag.
        """
        # Send request to RFID tag
        status = self.request(mode=self.config.PICC_CMD_REQA)
        if status != self.status.OK:
            return None

        # Anticollision, return UID if successful
        status, uid = self.anticollision()
        if status != self.status.OK:
            return None

        return uid

    def read_tags(self):
        # Send request to RFID tag
        status = self.request(mode=self.config.PICC_CMD_REQA)
        if status != self.status.OK:
            return None

        # logging.info("############ Reag tag begin")
        detected_tags = self.detect_multiple_tags()
        for uid in detected_tags:
            # logging.info("############ Found tag:", bytes(uid).hex())
            logging.info("############ Found tag: ", uid)

        # status, uid = (
        #     self.anticollision_with_lvl(self.config.PICC_CMD_SEL_CL1))
        # logging.info(f"############ status={status}, uid={uid}")

        # status, uid = self.anticollision()
        # logging.info(f"############ status={status}, uid={uid}")

    def _prepare(self):
        """
        Prepare for Read/Write blocks in Tag.
        1. Request
        2. Anti-collision
        3. Select
        4. Authenticate <- need block_num, do in bussiness func

        Before Reading/Writing a new Sector, must be called everytime.
        """
        # Send request to RFID tag
        status = self.request(mode=self.config.PICC_CMD_REQA)
        if status != self.status.OK:
            return None

        # Anticollision, return UID if successful
        status, uid = self.anticollision()
        if status != self.status.OK:
            return None

        if uid:
            # Select the RFID tag
            self.picc_select(uid)

        return uid

    def read_block_init(self, block_num):
        """
        Read data from the RFID tag.
        """
        # Check block is valid
        assert block_num in range(64), f"block {block_num} is out of range(64)"

        uid = self._prepare()
        if not uid:
            return None, None

        # Authenticate with the tag using the provided key
        status = self.pcd_authenticate(
            auth_mode=self.config.PICC_CMD_AUTH_KEY_A,
            block_num=block_num, auth_key=self.auth_key, uid=uid)

        # Initialize variables for storing data and text read from the tag
        block_data_s = None

        if status == self.status.OK:
            try:
                # Read data blocks specified by block_addr
                block_data = self.read_block(block_num)

                if block_data:
                    # Convert to string
                    block_data_s = self.format_block_data(block_data)

            except Exception as e:
                # logging.error(f"Error: {e}")
                pass

        # Stop cryptographic communication with the tag
        self.pcd_stop_crypto_1()

        # Return the tag ID and the read data
        return uid, block_data_s

    def read_single_block(self, uid, block_num):
        """
        Read data from the RFID tag sector.
        """
        assert uid, f"Read sector get error UID: {uid}"

         # Check block is valid
        assert block_num in range(64), f"block {block_num} is out of range(64)"

        # Authenticate with the tag using the provided key
        status = self.pcd_authenticate(
            auth_mode=self.config.PICC_CMD_AUTH_KEY_A,
            block_num=block_num, auth_key=self.auth_key, uid=uid)

        # Initialize variables for storing data and text read from the tag
        block_data_s = None

        if status == self.status.OK:
            try:
                # Read data blocks specified by block_addr
                block_data = self.read_block(block_num)

                if block_data:
                    # Convert to string
                    block_data_s = self.format_block_data(block_data)

            except Exception as e:
                logging.error(f"read_single_block Error: {e}")

        # Stop cryptographic communication with the tag
        self.pcd_stop_crypto_1()

        # Return the tag ID and the read data
        return block_data_s

    def read_sector(self, uid, sector_num):
        """
        Read data from the RFID tag sector.
        """
        assert uid, f"Read sector get error UID: {uid}"

        # Check sector is valid
        assert sector_num in range(16), (
            f"sector {sector_num} is out of range(16)")

        block_data_lst = []
        block_num_lst = range(sector_num*4, sector_num*4+4)

        # Authenticate with the tag using the provided key
        self.pcd_authenticate(auth_mode=self.config.PICC_CMD_AUTH_KEY_A,
            block_num=block_num_lst[0], auth_key=self.auth_key, uid=uid)

        for block_num in block_num_lst:
            try:
                # Read data blocks specified by block_addr
                block_data = self.read_block(block_num)

                if block_data:
                    # Convert to string
                    block_data_s = self.format_block_data(block_data)
                    block_data_lst.append((block_num, block_data_s))

            except Exception as e:
                # logging.error(f"Error: {e}")
                break

        # Stop cryptographic communication with the tag
        self.pcd_stop_crypto_1()

        return block_data_lst

    def read_all_blocks(self, uid):
        """
        Read all blocks data from the RFID tag.
        """
        assert uid, f"Read all blocks get error UID: {uid}"

        block_num_lst = range(64)
        block_data_lst = []

        for block_num in block_num_lst:
            if block_num % 4 == 0:
                # Authenticate with the tag using the provided key
                self.pcd_authenticate(
                    auth_mode=self.config.PICC_CMD_AUTH_KEY_A,
                    block_num=block_num, auth_key=self.auth_key, uid=uid)

            try:
                # Read data blocks specified by block_addr
                block_data = self.read_block(block_num)

                if block_data:
                    # Convert to string
                    block_data_s = self.format_block_data(block_data)
                    block_data_lst.append((block_num, block_data_s))

            except Exception as e:
                # logging.error(f"Error: {e}")
                break

        # Stop cryptographic communication with the tag
        self.pcd_stop_crypto_1()

        return block_data_lst

    def write_single_block(self, uid, block_num, data):
        """
        Writes 16 bytes to the active PICC.
        For MIFARE Classic the sector containing the block
        must be authenticated before calling this function.
        """
        assert uid, f"Write block single get error UID: {uid}"

        # Check sector is valid
        assert block_num in range(64), f"block {block_num} is out of range(64)"

        # Authenticate with the tag using the provided key
        self.pcd_authenticate(auth_mode=self.config.PICC_CMD_AUTH_KEY_A,
            block_num=block_num, auth_key=self.auth_key, uid=uid)

        # Read data blocks specified by block_addr
        res = self.write_block(block_num, data)

        # Stop cryptographic communication with the tag
        self.pcd_stop_crypto_1()

        return

    def cal_blocks_sha256(self, uid):
        """
        Calculate the SHA-256 of Sector 0~14.
        Sector 15 is the Sector to write result, pass.
        """
        block_data_lst = self.read_all_blocks(uid)

        assistant = HashAssistant()
        # Get the data from block 0 to block 59
        data_string = assistant.block_to_string(block_data_lst[:60])
        sha256_hash_lst = assistant.hash_as_list(data_string)

        return sha256_hash_lst

    # Loops
    def read_uid_loop(self):
        """
        Read the tag ID from the RFID tag.
        """
        for _ in range(self.retry_times):
            uid = self.read_uid()
            if uid:
                return uid
        return None

    def read_block_init_loop(self, block_num):
        """
        Read the block data initially from the RFID tag.
        """
        for _ in range(self.retry_times):
            uid, block_data = self.read_block_init(block_num)
            if uid:
                return uid, block_data
        return None, None

    def read_all_loop(self, uid):
        for _ in range(self.retry_times):
            block_data = self.read_all_blocks(uid)
            if block_data:
                return block_data
        return None

    def prepare_loop(self):
        """
        Begin the prepare loop, wait for the tag.
        """
        for _ in range(self.retry_times):
            uid = self._prepare()
            if uid:
                return uid
        return None


# ========================================================================
# ---- Sample classes for testing----
# MFRC522Service
# RFIDManager
# MFRC522
# ========================================================================

class MFRC522Service:
    def __init__(self, reactor):
        self.reactor = reactor

        self.running = False
        self.timer = None

        self.func = None
        self.params = None
        self.callback = None

        # Reschedule interval, seconds
        self.period = 0.25

    def start(self):
        if self.running:
            return False
        self.running = True

        waketime = self.reactor.monotonic() + self.period
        self.timer = self.reactor.register_timer(
            self.periodic_task, waketime)

        # logging.info("Service started")
        return True

    def stop(self):
        if not self.running:
            return False
        self.running = False

        self.teardown()

        # logging.info("Service stop")
        return True

    def schedule(self, func, params=None, callback=None):
        if self.func and self.running:
            # Return to skip
            logging.warning(
                f"schedule func:{self.func} exists and running, skip...")
            return False

        self.func = func
        self.params = params
        self.callback = callback

    def teardown(self):
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None

        if self.func:
            self.func = None
            self.params = None

    def periodic_task(self, eventtime):
        # logging.info(f"Periodic task executed at {eventtime}")
        if self.func is None:
            logging.warning(f"Schedule func not exists, return")
            return self.reactor.NEVER

        if self.timer is None:
            logging.warning(f"Schedule timer not exists, return")
            return self.reactor.NEVER

        result = self.func(**self.params) \
            if self.params is not None \
            else self.func()

        if result and self.callback:
            self.callback(result)

        # Re-register the timer for the next execution
        next_waketime = self.reactor.monotonic() + self.period

        if self.timer:
            self.reactor.update_timer(self.timer, next_waketime)
            # logging.info(f"Periodic task next_waketime: {next_waketime}")
            return next_waketime
        else:
            logging.info(f"Schedule timer not exists, return Never")
            return self.reactor.NEVER


class RFIDManager:
    def __init__(self, spi):
        self.spi = spi
        self.handler = MFRC522Handler(self.spi)

        # Default max_size=16
        self.cache = RFIDCache()

    def rfid_get_uid(self):
        # Get Version
        # version = self.handler.pcd_get_version()
        # logging.info(f"Firmware Version: {hex(version).upper().zfill(2)}")

        # Get UID
        uid = self.handler.prepare_loop()
        if not uid:
            logging.info("No Tag, return")
            return

        uid_s = self.handler.format_block_data(uid)
        # logging.info(f"Tag UID: {uid_s}")
        return uid_s

    def rfid_read_sector(self):
        # Read single sector
        sector_num = 15
        retry_times = 5

        for _ in range(retry_times):
            block_data_lst = self.handler.read_sector(uid, sector_num)

            if block_data_lst:
                block_data_lst.sort(reverse=True)

                logging.info(f"Sector: {sector_num}")
                for i,data in block_data_lst:
                    logging.info(f"Block {i}: {data}")

                break

    def rfid_read_all_blocks(self):
        # Read all blocks
        uid = self.handler.prepare_loop()
        if not uid:
            return

        uid_s = self.handler.format_block_data(uid)
        logging.info(f"Card UID: {uid_s}")

        block_data_lst = self.handler.read_all_loop(uid)

        if block_data_lst:
            # block_data_lst.sort(reverse=True)
            for i,data in block_data_lst:
                logging.info(f"Block {i}: {data}")

    def rfid_block_init(self):
        # Read single block from init
        block_num = 0
        uid, block_data = self.handler.read_block_init_loop(block_num)
        logging.info(f"Block {block_num}: {block_data}")

    def rfid_write_block(self, block_num, byte_array):
        # Write single block
        uid = self.handler.prepare_loop()
        if not uid:
            return

        uid_s = self.handler.format_block_data(uid)
        logging.info(f"Card UID: {uid_s}")

        # block_num = 16
        # byte_array = [0x00,] * 16
        self.handler.write_single_block(uid, block_num, byte_array)

        uid = self.handler.prepare_loop()
        if uid:
            blocks_read = self.handler.read_single_block(uid, block_num)

            if blocks_read:
                logging.info(f"Block {block_num}: {blocks_read}")

    def rfid_write_hash(self):
        # Calculate hash block data and write into block 60/61
        uid = self.handler.prepare_loop()
        if not uid:
            return

        uid_s = self.handler.format_block_data(uid)
        logging.info(f"Card UID: {uid_s}")

        sha256_data_lst = self.handler.cal_blocks_sha256(uid)

        block_num = 60
        data = sha256_data_lst[:16]
        self.handler.prepare_loop()
        self.handler.write_single_block(uid, block_num, data)

        block_num = 61
        data = sha256_data_lst[16:]
        self.handler.prepare_loop()
        self.handler.write_single_block(uid, block_num, data)

    def rfid_read(self):
        """
        hash_read
            hash data read from Tag
        hash_cached
            hash data read from Cached
        hash_calculate
            hash data calculate from full blocks data read from Tag
        """
        # Get Version
        # version = self.handler.pcd_get_version()
        # logging.info(f"Firmware Version: {hex(version).upper().zfill(2)}")

        uid = self.handler.prepare_loop()
        if not uid:
            logging.info("No Tag, return")
            return

        uid_s = self.handler.format_block_data(uid)
        # logging.info(f"Tag uid={uid_s}")

        # Read the Sector 15 to get hash data, prepare have done before
        sector_15_lst = self.handler.read_sector(uid=uid, sector_num=15)
        sector_15_lst.sort(key=lambda tup: tup[0])
        # Filter block 60 & block 61 data
        blocks_lst = list(filter(lambda tup: tup[0] in [60, 61], sector_15_lst))

        assistant = HashAssistant()
        # Block data to string
        hash_read = assistant.block_to_string(blocks_lst)
        logging.info(f"hash_read: {hash_read}")

        # Validation schema
        if not hash_read:
            logging.error(f"Hash block read error with UID: {uid_s}")
            return

        if not assistant.is_valid_length(hash_read):
            logging.error(f"The hash data has wrong length: {hash_read}")
            return

        if not assistant.is_hexadecimal(hash_read):
            logging.error(f"The hash data is not hex: {hash_read}")
            return

        if assistant.has_high_zero_ratio(hash_read):
            logging.error(f"The hash data has high zero ratio: {hash_read}")
            return

        # Get cached blocks data by uid string
        cache_key = self.cache.gen_key(uid_s)
        blocks_cached = self.cache.get(cache_key)
        # Reload flag init False
        need_reload = False

        if blocks_cached:
            logging.info("Cache load.")

            # If cached blocks exists, find the cached block 60/61 first
            blocks_cached.sort(key=lambda tup: tup[0])
            blocks_hash = list(
                filter(lambda tup: tup[0] in [60, 61], blocks_cached))

            hash_cached = assistant.block_to_string(blocks_hash)
            logging.info(f"hash_cached: {hash_cached}")

            if hash_read == hash_cached:
                # Read and cached hash data are the same, return cached data
                logging.info(
                    "Cached found and hash match, return blocks cached.")
                # for i,data in blocks_cached:
                #     logging.info(f"Block {i}: {data}")

                cache_key = self.cache.gen_key(uid_s, prefix="rfid_dict")
                rfid_model_json = self.cache.get(cache_key)
                # logging.info(rfid_model_json)
                return rfid_model_json

            else:
                # Read and cached hash data are different, reload new block data
                logging.info("Cache not the same, reload.")
                need_reload = True
        else:
            # No cached found, reload new block data
            logging.info("Init load.")
            need_reload = True

        if need_reload:
            # Reload begin, prepare before read
            uid_new = self.handler.prepare_loop()
            if not uid_new:
                logging.info("No Tag, reload failed, exit.")
                return

            uid_new_s = self.handler.format_block_data(uid_new)

            # If new UID is not the same UID of begin,
            # a new Tag collision problem may happen, exit
            if uid_new_s != uid_s:
                logging.info(f"UID begin: {uid_s}")
                logging.info(f"UID current: {uid_new_s}")
                logging.info(f"Found different UID, reload failed, exit.")
                return

            # Read full blocks data
            blocks_read = self.handler.read_all_loop(uid)

            if blocks_read:
                # Calculate and check hash_block is valid

                # Get the data from block 0 to block 59
                blocks_read.sort(key=lambda tup: tup[0])
                data_string = assistant.block_to_string(blocks_read[:60])
                hash_calculate = assistant.hash_as_string(data_string)
                logging.info(f"hash_calculate: {hash_calculate}")

                # Validation check
                if hash_read != hash_calculate:
                    logging.error(
                        "Read hash block data not equal to calculated, exit.")
                    return

                # Cached the full blocks data
                cache_key = self.cache.gen_key(uid_s)
                self.cache.add(cache_key, blocks_read)
                logging.info(f"RFID data success cached with UID: {uid_s}")
                # for i,data in blocks_read:
                #     logging.info(f"Block {i}: {data}")

                blocks_dct = {str(tup[0]):tup[1].replace(" ", "")
                              for tup in blocks_read}
                # logging.info(f"blocks_dct: {blocks_dct}")

                rfid_model = RFIDModel()
                rfid_model.from_blocks(blocks_dct)
                rfid_model_json = rfid_model.to_json()

                cache_key = self.cache.gen_key(uid_s, prefix="rfid_dict")
                self.cache.add(cache_key, rfid_model_json)
                # logging.info(rfid_model_json)
                return rfid_model_json

            else:
                logging.info(
                    "Failed to Read all blocks data while reloading, exit.")

        return


class MFRC522:
    """
    Printer class that controls RFID sensor
    """
    def __init__(self, config):
        self.printer = config.get_printer()
        self.spi = MCU_SPI_from_config(
            config=config,
            mode=0,
            pin_option="cs_pin",
            default_speed=5000000,
            share_type=None,
            cs_active_high=False)

        # self.mcu = self.spi.get_mcu()
        # self.oid = self.mcu.create_oid()

        self.rfid_manager = None
        self.service = None

        name = config.get_name().split()[1]
        gcode = self.printer.lookup_object("gcode")
        # gcode.register_command("RFID", self.cmd_RFID)
        # gcode.register_command("RFID_WRITE", self.cmd_RFID_write)

        gcode.register_mux_command(
            cmd = "RFID",
            key = "NAME",
            value = name,
            func = self.cmd_RFID)

        gcode.register_mux_command(
            cmd = "RFID_WRITE",
            key = "NAME",
            value = name,
            func = self.cmd_RFID_write)

    def _init_service(self):
        self.rfid_manager = RFIDManager(self.spi)
        self.service = MFRC522Service(self.printer.get_reactor())

    def read_begin(self):
        if not self.service:
            self._init_service()

        self.service.schedule(func=self.rfid_manager.rfid_read)

        ret = self.service.start()
        msg = "RFID read initiated in the backend." \
            if ret else "RFID read is already running."
        logging.info(msg)

    def read_end(self):
        if not self.service:
            logging.warning("No service found, return")
            return

        ret = self.service.stop()
        msg = "RFID read terminated in the backend." \
            if ret else "RFID read is not running."
        logging.info(msg)

    def write(self):
        data = {
            "tag_version": 1000,
            "filament_manufacturer": "BQ Tech",
            "manufacture_datetime": "20240812_162600",
            "filament_material_type": "PET",
            "filament_type_detailed": "PET (CEP)",
            "serial_number": "IP243ZCXV67",
            "color_code": "FFFFFF",
            "color_name_a": "Corn Flower Blue",
            "color_name_b": "",
            "filament_diameter": 1750,
            "density": 1240,
            "shrinkage": 100,
            "flow_ratio": 98,
            "max_volumetric_speed": 12,
            "minimal_purge_on_wipe_tower": 15,
            "spool_material_type": "Plastic",
            "spool_weight": 1000,
            "spool_empty_weight": 260,
            "spool_outer_diameter": 200,
            "spool_inner_diameter": 52,
            "spool_width": 67,
            "drying_time": 120,
            "drying_temperature_min": 25,
            "drying_temperature_max": 60,
            "bed_temerature_min": 25,
            "bed_temerature_max": 60,
            "printing_temperature_min": 200,
            "printing_temperature_max": 240,
            "softening_temperature": 60,
            "bed_temperature": 60,
            "print_speed_min": 30,
            "print_speed_max": 600,
            "nozzle_diameter": 20
        }

        rfid_model = RFIDModel()
        rfid_model.from_dict(data)
        # data_encode_json = rfid_model.to_json()
        # logging.info(f"data_encode_json: {data_encode_json}")

        prepared_blocks = rfid_model.prepare_blocks_writing()
        # prepared_blocks_json = json.dumps(prepared_blocks, indent=4)
        # logging.info(f"prepared_blocks: {prepared_blocks_json}")

        rfid_manager = RFIDManager(self.spi)

        for block_num, byte_array in prepared_blocks.items():
            rfid_manager.rfid_write_block(block_num, byte_array)

        rfid_manager.rfid_write_hash()

        return

    def detect_begin(self):
        if not self.service:
            self._init_service()

        self.service.schedule(func=self.rfid_manager.rfid_get_uid)

        ret = self.service.start()
        msg = "RFID detect initiated in the backend." \
            if ret else "RFID detect is already running."
        logging.info(msg)

    def detect_end(self):
        if not self.service:
            logging.warning("No service found, return")
            return

        ret = self.service.stop()
        msg = "RFID detect terminated in the backend." \
            if ret else "RFID detect is not running."
        logging.info(msg)

    # CMD func for G-Code
    def cmd_RFID(self, gcmd):
        flag = gcmd.get_int("READ", 0)
        if flag == 1:
            self.read_begin()
        else:
            self.read_end()

    def cmd_RFID_write(self, gcmd):
        self.write()


# def load_config(config):
#     return MFRC522(config)


# def load_config_prefix(config):
#     return MFRC522(config)
