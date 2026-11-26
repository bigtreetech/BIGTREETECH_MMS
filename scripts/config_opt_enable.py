import sys, json
from configparser import NoOptionError
from config_editor import ConfigEditor
from config_editor import strip_comment

def config_option_enable(filename, option_line, uncomment=True):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        if (option_line < 0 or option_line >= len(lines)):
            print(f"Error[{filename}]: option_line:{option_line}, lines:{len(lines)}")
            return False

        replaced_lines = []
        for i, line in enumerate(lines):
            if i == option_line:
                add_line = strip_comment(line)
                if uncomment != True:
                    add_line = "# " + add_line
                replaced_lines.append(add_line)
            else:
                replaced_lines.append(line)

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
        print(f"Error[config_uncomment]: {args_count} args.\nThere must be 3 args for 'new config path', 'old config path' and 'custom config'.")
        sys.exit(1)

    new_filename = str(sys.argv[1])
    old_filename = str(sys.argv[2])
    config_str = str(sys.argv[3])

    try:
        config_json = json.loads(config_str)
    except json.JSONDecodeError as e:
        print("Error[config_uncomment]: Config Json: ", str(e))
        sys.exit(1)

    try:
        new_mms = ConfigEditor(new_filename)
        if old_filename != "":
            old_mms = ConfigEditor(old_filename)
            for section in old_mms.fileconfig.sections():
                if section.startswith("gcode_macro "):
                    continue
                for option in old_mms.fileconfig.options(section):
                    config_val = config_json.get(section, {}).get(option, None)
                    # option in config, so skip copy from old files
                    if config_val is not None:
                        continue
                    try:
                        new_mms.fileconfig.get(section, option)
                    except NoOptionError:
                        filename = new_mms.get_filename(section)
                        if filename is None:
                            continue
                        pos = new_mms.get_option_val_position(filename, section, option, True)
                        if pos["option_line"] >= 0 and not config_option_enable(
                            filename, pos["option_line"], True
                        ):
                            raise RuntimeError(f"Could not enable [{section}] {option}")

        for section, option_dict in config_json.items():
            for option, value in option_dict.items():
                value = (value == "1")
                new_true = False
                try:
                    new_mms.fileconfig.get(section, option)
                    new_true = True
                except NoOptionError as e:
                    new_true = False
                if value != new_true:
                    filename = new_mms.get_filename(section, None)
                    pos = new_mms.get_option_val_position(filename, section, option, True)
                    if not config_option_enable(filename, pos["option_line"], value):
                        raise RuntimeError(f"Could not set [{section}] {option}")

    except Exception as e:
        sys.exit(f"Error[config_uncomment]: {e}")
