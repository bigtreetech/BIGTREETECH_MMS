import sys, json
from config_editor import ConfigEditor

def is_outdated_val(section, option, old_val):
    outdated_str = '''{
        "temperature_sensor ViViD_Dryer_L": {
            "i2c_software_scl_pin": "vivid:PA7",
            "i2c_software_sda_pin": "vivid:PA6"
        },
        "temperature_sensor ViViD_Dryer_R": {
            "i2c_software_scl_pin": "vivid:PA9",
            "i2c_software_sda_pin": "vivid:PA10"
        },
        "heater_generic ViViD_Dryer": {
            "combination_method": "mean",
            "maximum_deviation": "20"
        },
        "gcode_macro MMS_DISABLE": {
            "gcode": "\
\\nMMS_STOP\
\\nMANUAL_STEPPER STEPPER=selector_stepper ENABLE=0\
\\nMANUAL_STEPPER STEPPER=drive_stepper ENABLE=0\
\\nSET_HEATER_TEMPERATURE HEATER=ViViD_Dryer TARGET=0\
\\nRESPOND TYPE=echo MSG='MMS DISABLE finish'"
        },
        "manual_stepper selector_stepper": {
            "rotation_distance": "360"
        }
    }'''
    try:
        outdated_json = json.loads(outdated_str)
        outdated_val = outdated_json.get(section, {}).get(option, None)
        if outdated_val is not None:
            outdated_val = str(outdated_val)
            if (outdated_val == old_val):
                return True
    except json.JSONDecodeError as e:
        print("Error Outdated Json: ", str(e))
    return False

def config_replace_option_val(filename, pos, new_val):
    val_start_line = pos["val_start_line"]
    val_start_byte = pos["val_start_byte"]
    val_end_line = pos["val_end_line"]
    val_end_byte = pos["val_end_byte"]
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        if (val_start_line < 0 or val_end_line >= len(lines) or val_start_line > val_end_line):
            print(f"Error[{filename}]: val_start_line:{val_start_line}, val_end_line:{val_end_line}, lines:{len(lines)}")
            return False
        if (val_start_byte < 0 or val_end_byte < 0):
            print(f"Error[{filename}]: val_start_byte:{val_start_byte}, val_end_byte:{val_end_byte}")
            return False
        if (val_start_line == val_end_line and val_start_byte > val_end_byte):
            print(f"Error[{filename}]: val_start_line:{val_start_line}, val_end_line:{val_end_line}, val_start_byte:{val_start_byte}, val_end_byte:{val_end_byte}")
            return False

        replaced_lines = []
        for i, line in enumerate(lines):
            if i < val_start_line or i > val_end_line:
                replaced_lines.append(line)
            if i == val_start_line:
                replaced_lines.append(line[:val_start_byte])
                add_lines = new_val.strip().split('\n')
                for j, add_line in enumerate(add_lines):
                    if (j != 0):
                        replaced_lines.append("\n  ")
                    replaced_lines.append(add_line)
            if i == val_end_line:
                replaced_lines.append(line[val_end_byte:])

        with open(filename, 'w', encoding='utf-8') as f:
            f.writelines(replaced_lines)
        return True
    except FileNotFoundError:
        print(f"Error[{filename}]: cannot open file")
        return False
    except Exception as e:
        print(f"Error[{filename}]: {e}")
        return False

if __name__ == "__main__":
    args_count = len(sys.argv) - 1
    if args_count != 3:
        print(f"Error[config_copy]: {args_count} args.\nThere must be 3 args for 'new config path', 'old config path' and 'custom config'.")
        sys.exit(1)

    new_filename = str(sys.argv[1])
    old_filename = str(sys.argv[2])
    config_str = str(sys.argv[3])

    try:
        config_json = json.loads(config_str)
    except json.JSONDecodeError as e:
        print("Error[config_copy]: Config Json: ", str(e))
        sys.exit(1)

    try:
        new_mms = ConfigEditor(new_filename)
        if old_filename != "":
            old_mms = ConfigEditor(old_filename)
        else:
            old_mms = None

        for section in new_mms.fileconfig.sections():
            # The shipped macro definitions belong to the new MMS code.
            if section.startswith("gcode_macro "):
                continue
            for option in new_mms.fileconfig.options(section):
                try:
                    new_val = new_mms.fileconfig.get(section, option)
                except Exception as e:
                    print(f"Error[config_copy][new]: {e}")
                    sys.exit(1)

                old_val = config_json.get(section, {}).get(option, None)
                if old_val is None:
                    if old_mms is None:
                        continue
                    else:
                        try:
                            old_val = old_mms.fileconfig.get(section, option)
                        except Exception as e:
                            print(f"Info[config_copy][old]: {e}")
                            continue
                else:
                    old_val = str(old_val)

                if new_val != old_val:
                    if (is_outdated_val(section, option, old_val)):
                        continue
                    filename = new_mms.get_filename(section, option)
                    pos = new_mms.get_option_val_position(filename, section, option, False)
                    if not config_replace_option_val(filename, pos, old_val):
                        raise RuntimeError(f"Could not migrate [{section}] {option}")

        # A failed migration must never replace the installed configuration.
        ConfigEditor(new_filename)
    except Exception as e:
        sys.exit(f"Error[config_copy]: {e}")
