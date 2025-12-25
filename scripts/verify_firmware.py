import sys, json, zlib

def read_file_binary(bin_path):
    try:
        with open(bin_path, 'rb') as f:
            bin_data = f.read()
            return bin_data
    except FileNotFoundError:
        print(f"{bin_path}: path does not exist!")
        return None
    except PermissionError:
        print(f"{bin_path}: no read permission!")
        return None
    except Exception as e:
        print(f"{bin_path}: unknown error!")
        return None

def check_binary(mcu_dict, bin_path):
    """
    Extract klipper.dict from binary
    """

    bin_data = read_file_binary(bin_path)
    if bin_data is None:
        return False

    klipper_dict: Dict[str, Any] = {}
    for idx in range(len(bin_data)):
        try:
            uncmp_data = zlib.decompress(bin_data[idx:])
            klipper_dict = json.loads(uncmp_data)
        except (zlib.error, json.JSONDecodeError):
            continue
        if klipper_dict.get("app") == "Klipper":
            break
    if klipper_dict:
        ver = klipper_dict.get("version", "")
        config = klipper_dict.get("config", {})
        error = False
        print(f"Detected Klipper binary version {ver}:")
        for key in mcu_dict:
            mcu_v = mcu_dict[key]
            bin_v = config.get(key, "")
            if mcu_v != bin_v:
                error = True
            print(f"  {key}:")
            print(f"    Must be : {mcu_v}")
            print(f"    Detected: {bin_v}")
        return (error == False)

    print("Invalid Klipper firmware binary file")
    return False

stm32g0b1xx_dict = {
    "MCU": "stm32g0b1xx",
    "CLOCK_FREQ": 64000000,
    "RESERVE_PINS_USB": "PA11,PA12"
}
stm32f042x6_dict = {
    "MCU": "stm32f042x6",
    "CLOCK_FREQ": 48000000,
    "RESERVE_PINS_USB": "PA11,PA12"
}

if __name__ == "__main__":
    args_count = len(sys.argv) - 1
    if args_count != 2:
        print(f"Error: only {args_count} args.\n There must be 2 args, The first is 'stm32g0b1xx' or 'stm32f042x6', the second is the file path.")
        sys.exit(1)

    mcu = str(sys.argv[1])
    if mcu == 'stm32g0b1xx':
        mcu_dict = stm32g0b1xx_dict
    elif mcu == 'stm32f042x6':
        mcu_dict = stm32f042x6_dict
    else:
        print(f"Error: wrong mcu '{mcu}', mcu must be 'stm32g0b1xx' or 'stm32f042x6'")
        sys.exit(1)

    path = str(sys.argv[2])
    if check_binary(mcu_dict, path):
        sys.exit(0)
    else:
        sys.exit(1)
