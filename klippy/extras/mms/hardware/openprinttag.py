# Support for OpenPrintTag (CBOR + NDEF)
#
# Copyright (C) 2026 Florian Stamer <florian@stamer.dev>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import struct

class CBORDecoder:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def decode(self):
        if self.pos >= len(self.data):
            return None
        
        byte = self.data[self.pos]
        self.pos += 1
        major = byte >> 5
        additional = byte & 0x1F

        if major == 0: # unsigned integer
            return self._decode_uint(additional)
        elif major == 1: # negative integer
            return -1 - self._decode_uint(additional)
        elif major == 2: # byte string
            length = self._decode_uint(additional)
            res = self.data[self.pos:self.pos+length]
            self.pos += length
            return res
        elif major == 3: # text string
            length = self._decode_uint(additional)
            res = self.data[self.pos:self.pos+length].decode("utf-8", "ignore")
            self.pos += length
            return res
        elif major == 4: # array
            if additional == 31: # indefinite length
                items = []
                while self.pos < len(self.data):
                    if self.data[self.pos] == 0xFF: # break code
                        self.pos += 1
                        break
                    items.append(self.decode())
                return items
            length = self._decode_uint(additional)
            return [self.decode() for _ in range(length)]
        elif major == 5: # map
            if additional == 31: # indefinite length
                res = {}
                while self.pos < len(self.data):
                    if self.data[self.pos] == 0xFF: # break code
                        self.pos += 1
                        break
                    key = self.decode()
                    val = self.decode()
                    if key is not None:
                        res[key] = val
                return res
            length = self._decode_uint(additional)
            res = {}
            for _ in range(length):
                key = self.decode()
                val = self.decode()
                if key is not None:
                    res[key] = val
            return res
        elif major == 6: # tag
            self._decode_uint(additional) # skip tag
            return self.decode()
        elif major == 7: # float/simple
            if additional == 20: return False
            if additional == 21: return True
            if additional == 22: return None
            if additional == 26: # float32
                res = struct.unpack(">f", self.data[self.pos:self.pos+4])[0]
                self.pos += 4
                return res
            if additional == 27: # float64
                res = struct.unpack(">d", self.data[self.pos:self.pos+8])[0]
                self.pos += 8
                return res
            if additional < 24: return additional
        
        return None

    def _decode_uint(self, additional):
        if additional < 24:
            return additional
        elif additional == 24:
            res = self.data[self.pos]
            self.pos += 1
            return res
        elif additional == 25:
            res = (self.data[self.pos] << 8) | self.data[self.pos+1]
            self.pos += 2
            return res
        elif additional == 26:
            res = (self.data[self.pos] << 24) | (self.data[self.pos+1] << 16) | \
                  (self.data[self.pos+2] << 8) | self.data[self.pos+3]
            self.pos += 4
            return res
        elif additional == 27:
            res = struct.unpack(">Q", self.data[self.pos:self.pos+8])[0]
            self.pos += 8
            return res
        return 0

class OPTDecoder:
    # Material Type ID mapping from official OpenPrintTag spec v1
    MATERIAL_TYPES = {
        0: "PLA",
        1: "PETG",
        2: "ABS",
        3: "ASA",
        4: "PC",
        5: "PP",
        6: "PA",
        7: "TPU",
        8: "HIPS",
        9: "PVA",
        10: "BVOH",
        11: "PVB",
        12: "PET",
    }

    def __init__(self):
        pass

    def parse_ndef(self, data):
        # Find NDEF Message TLV (03)
        pos = 0
        while pos < len(data):
            if data[pos] == 0x03:
                pos += 1
                length = data[pos]
                if length == 0xFF:
                    length = (data[pos+1] << 8) | data[pos+2]
                    pos += 3
                else:
                    pos += 1
                break
            pos += 1
        else:
            return None

        if pos >= len(data): return None
        header = data[pos]
        pos += 1
        sr = (header >> 4) & 0x01
        type_len = data[pos]
        pos += 1
        
        if sr:
            payload_len = data[pos]
            pos += 1
        else:
            payload_len = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4
            
        type_name = data[pos:pos+type_len].decode("utf-8", "ignore")
        pos += type_len
        
        if "openprinttag" in type_name.lower():
            return data[pos:pos+payload_len]
            
        return None

    def decode(self, raw_data):
        payload = self.parse_ndef(raw_data)
        if not payload:
            payload = raw_data
            
        try:
            decoder = CBORDecoder(payload)
            full_data = {}
            while decoder.pos < len(payload):
                obj = decoder.decode()
                if isinstance(obj, dict):
                    full_data.update(obj)
                else:
                    break
            return full_data
        except Exception as e:
            logging.error(f"OPT: Failed to decode CBOR: {e}")
            return None

    def map_to_mms(self, opt_data):
        if not isinstance(opt_data, dict) or not opt_data:
            return None
            
        # Strict mapping according to OpenPrintTag v1 Spec
        # 11: brand_name, 10: material_name, 9: material_type (enum)
        
        vendor = opt_data.get(11)
        name = opt_data.get(10)
        
        mat_id = opt_data.get(9)
        material = self.MATERIAL_TYPES.get(mat_id, "GENERIC")
        
        # Color (Key 19 - color_rgba)
        color = None
        c19 = opt_data.get(19)
        if isinstance(c19, (bytes, bytearray)) and len(c19) >= 3:
            color = c19[:3].hex().upper()
        elif isinstance(c19, list) and len(c19) >= 3:
            color = "".join([f"{c:02X}" for c in c19[:3]])
            
        # Fallback to Key 64 (color map)
        if not color:
            color_map = opt_data.get(64)
            if isinstance(color_map, dict) and color_map.get(0) == 0:
                val = color_map.get(1)
                if isinstance(val, (bytes, bytearray)) and len(val) >= 3:
                    color = val[:3].hex().upper()
        
        if not color:
            color = "607D8B"
        
        # Final result using MMS internal keys
        res = {
            "filament_manufacturer": vendor,
            "filament_type_detailed": name,
            "filament_material_type": material,
            "color_code": color,
        }
        
        # Temps (37: min_bed, 34: min_print)
        try:
            bt = opt_data.get(37)
            if bt is not None: res["bed_temperature"] = float(bt[0]) if isinstance(bt, list) else float(bt)
            
            nt = opt_data.get(34)
            if nt is not None: res["nozzle_temp"] = float(nt[0]) if isinstance(nt, list) else float(nt)
        except Exception:
            pass

        return res

class CBOREncoder:
    def __init__(self):
        self.data = bytearray()

    def encode(self, obj):
        if isinstance(obj, int):
            if obj >= 0:
                self._encode_type(0, obj)
            else:
                self._encode_type(1, -1 - obj)
        elif isinstance(obj, bytes) or isinstance(obj, bytearray):
            self._encode_type(2, len(obj))
            self.data.extend(obj)
        elif isinstance(obj, str):
            utf8_data = obj.encode('utf-8')
            self._encode_type(3, len(utf8_data))
            self.data.extend(utf8_data)
        elif isinstance(obj, list):
            self._encode_type(4, len(obj))
            for item in obj:
                self.encode(item)
        elif isinstance(obj, dict):
            self._encode_type(5, len(obj))
            for key, val in obj.items():
                self.encode(key)
                self.encode(val)
        elif isinstance(obj, float):
            # Float32 (simplified)
            self.data.append((7 << 5) | 26)
            self.data.extend(struct.pack(">f", obj))
        elif isinstance(obj, bool):
            self.data.append((7 << 5) | (21 if obj else 20))
        elif obj is None:
            self.data.append((7 << 5) | 22)
        return self.data

    def _encode_type(self, major, val):
        if val < 24:
            self.data.append((major << 5) | val)
        elif val < 256:
            self.data.append((major << 5) | 24)
            self.data.append(val)
        elif val < 65536:
            self.data.append((major << 5) | 25)
            self.data.extend(struct.pack(">H", val))
        elif val < 4294967296:
            self.data.append((major << 5) | 26)
            self.data.extend(struct.pack(">I", val))
        else:
            self.data.append((major << 5) | 27)
            self.data.extend(struct.pack(">Q", val))


class OPTEncoder:
    MATERIAL_TYPES_INV = {v: k for k, v in OPTDecoder.MATERIAL_TYPES.items()}

    # Mapping of OpenPrintTag string names to their integer keys
    KEY_MAPPING = {
        "instance_uuid": 0, "package_uuid": 1, "material_uuid": 2, "brand_uuid": 3,
        "gtin": 4, "brand_specific_instance_id": 5, "brand_specific_package_id": 6,
        "brand_specific_material_id": 7, "material_class": 8, "material_type": 9,
        "material_name": 10, "brand_name": 11, "write_protection": 13,
        "manufactured_date": 14, "expiration_date": 15, "nominal_netto_full_weight": 16,
        "actual_netto_full_weight": 17, "empty_container_weight": 18, "primary_color": 19,
        "secondary_color_0": 20, "secondary_color_1": 21, "secondary_color_2": 22,
        "secondary_color_3": 23, "secondary_color_4": 24, "transmission_distance": 27,
        "tags": 28, "density": 29, "filament_diameter": 30, "shore_hardness_a": 31,
        "shore_hardness_d": 32, "min_nozzle_diameter": 33, "min_print_temperature": 34,
        "max_print_temperature": 35, "preheat_temperature": 36, "min_bed_temperature": 37,
        "max_bed_temperature": 38, "min_chamber_temperature": 39, "max_chamber_temperature": 40,
        "chamber_temperature": 41, "container_width": 42, "container_outer_diameter": 43,
        "container_inner_diameter": 44, "container_hole_diameter": 45,
        "material_abbreviation": 52, "nominal_full_length": 53, "actual_full_length": 54,
        "country_of_origin": 55, "certifications": 56, "drying_temperature": 57,
        "drying_time": 58, "primary_color_lab": 59, "primary_color_ral": 60
    }

    def __init__(self):
        pass

    def map_from_mms(self, input_data):
        opt_data = {}
        
        # Handle standard OpenPrintTag keys
        for key, val in input_data.items():
            if key in self.KEY_MAPPING:
                cbor_key = self.KEY_MAPPING[key]
                
                # Handle special types
                if key.endswith("uuid") and isinstance(val, str):
                    import uuid
                    try:
                        opt_data[cbor_key] = uuid.UUID(val).bytes
                    except:
                        pass
                elif "color" in key and isinstance(val, str) and not key.endswith("lab") and not key.endswith("ral"):
                    val = val.replace("#", "")
                    if len(val) == 6:
                        opt_data[cbor_key] = bytes.fromhex(val + "FF")
                    elif len(val) == 8:
                        opt_data[cbor_key] = bytes.fromhex(val)
                elif key == "material_type" and isinstance(val, str):
                    opt_data[cbor_key] = self.MATERIAL_TYPES_INV.get(val.upper(), 0)
                else:
                    opt_data[cbor_key] = val

        # Handle legacy MMS keys (fallback mapping)
        if "filament_manufacturer" in input_data and 11 not in opt_data:
            opt_data[11] = input_data["filament_manufacturer"]
        if "filament_type_detailed" in input_data and 10 not in opt_data:
            opt_data[10] = input_data["filament_type_detailed"]
        if "filament_material_type" in input_data and 9 not in opt_data:
            opt_data[9] = self.MATERIAL_TYPES_INV.get(input_data["filament_material_type"], 0)
        if "color_code" in input_data and 19 not in opt_data:
            color = input_data["color_code"].replace("#", "")
            if len(color) == 6:
                opt_data[19] = bytes.fromhex(color + "FF")
            elif len(color) == 8:
                opt_data[19] = bytes.fromhex(color)
        if "bed_temperature" in input_data and 37 not in opt_data:
            opt_data[37] = int(input_data["bed_temperature"])
        if "nozzle_temp" in input_data and 34 not in opt_data:
            opt_data[34] = int(input_data["nozzle_temp"])

        # material_class is required; default to FFF (0 = filament)
        if 8 not in opt_data:
            opt_data[8] = 0

        return opt_data

    def encode(self, opt_data):
        # 1. Main Region: definite CBOR map (matches working tag format)
        main_enc = CBOREncoder()
        main_enc.encode(opt_data)
        main_data = bytes(main_enc.data)
        main_size = len(main_data)

        # 2. Meta Region: definite CBOR map {0: MAIN_OFF, 1: main_size}
        # meta key 0 = main_region_offset, key 1 = main_region_size (no aux region)
        # MAIN_OFF = meta encoding size (self-consistent):
        #   main_size <  24: A2 00 MM 01 SS       = 5 bytes → MAIN_OFF=5
        #   main_size < 256: A2 00 MM 01 18 SS     = 6 bytes → MAIN_OFF=6
        #   main_size <65536: A2 00 MM 01 19 SS SS = 7 bytes → MAIN_OFF=7
        if main_size < 24:
            MAIN_OFF = 5
        elif main_size < 256:
            MAIN_OFF = 6
        else:
            MAIN_OFF = 7
        meta_enc = CBOREncoder()
        meta_enc.encode({0: MAIN_OFF, 1: main_size})
        meta_data = bytes(meta_enc.data)

        # 3. CBOR payload = meta + main (no aux region)
        cbor_payload = bytearray()
        cbor_payload.extend(meta_data)  # bytes [0, MAIN_OFF)
        cbor_payload.extend(main_data)  # bytes [MAIN_OFF, MAIN_OFF+main_size)

        # 4. NDEF Record
        # The CC lives at NTAG page 3 and is NOT written here.
        # User memory (page 4) must start with the NDEF Message TLV (0x03).
        # Use SR=1 (Short Record, 1-byte payload length) when payload fits in 1 byte,
        # matching the format produced by reference OpenPrintTag implementations.
        type_str = b"application/vnd.openprinttag"
        cbor_len = len(cbor_payload)
        ndef_record = bytearray()
        if cbor_len <= 255:
            ndef_record.append(0xD2)                        # MB+ME+SR=1+TNF=MIME
            ndef_record.append(len(type_str))
            ndef_record.append(cbor_len)                    # 1-byte payload length
        else:
            ndef_record.append(0xC2)                        # MB+ME+SR=0+TNF=MIME
            ndef_record.append(len(type_str))
            ndef_record.extend(struct.pack(">I", cbor_len)) # 4-byte payload length
        ndef_record.extend(type_str)
        ndef_record.extend(cbor_payload)

        # 5. NDEF Message TLV (0x03) + Terminator (0xFE)
        ndef_len = len(ndef_record)
        result = bytearray()
        result.append(0x03)
        if ndef_len > 0xFE:
            result.append(0xFF)
            result.extend(struct.pack(">H", ndef_len))
        else:
            result.append(ndef_len)
        result.extend(ndef_record)
        result.append(0xFE)

        return result
