import sys, os, glob, configparser, io

error = configparser.Error


######################################################################
# Config section parsing helper
######################################################################

class sentinel:
    pass

class ConfigWrapper:
    error = configparser.Error
    def __init__(self, printer, fileconfig, access_tracking, section):
        self.printer = printer
        self.fileconfig = fileconfig
        self.access_tracking = access_tracking
        self.section = section
    def get_printer(self):
        return self.printer
    def get_name(self):
        return self.section
    def _get_wrapper(self, parser, option, default, minval=None, maxval=None,
                     above=None, below=None, note_valid=True):
        if not self.fileconfig.has_option(self.section, option):
            if default is not sentinel:
                if note_valid and default is not None:
                    acc_id = (self.section.lower(), option.lower())
                    self.access_tracking[acc_id] = default
                return default
            raise error("Option '%s' in section '%s' must be specified"
                        % (option, self.section))
        try:
            v = parser(self.section, option)
        except self.error as e:
            raise
        except:
            raise error("Unable to parse option '%s' in section '%s'"
                        % (option, self.section))
        if note_valid:
            self.access_tracking[(self.section.lower(), option.lower())] = v
        if minval is not None and v < minval:
            raise error("Option '%s' in section '%s' must have minimum of %s"
                        % (option, self.section, minval))
        if maxval is not None and v > maxval:
            raise error("Option '%s' in section '%s' must have maximum of %s"
                        % (option, self.section, maxval))
        if above is not None and v <= above:
            raise error("Option '%s' in section '%s' must be above %s"
                        % (option, self.section, above))
        if below is not None and v >= below:
            raise self.error("Option '%s' in section '%s' must be below %s"
                             % (option, self.section, below))
        return v
    def get(self, option, default=sentinel, note_valid=True):
        return self._get_wrapper(self.fileconfig.get, option, default,
                                 note_valid=note_valid)
    def getint(self, option, default=sentinel, minval=None, maxval=None,
               note_valid=True):
        return self._get_wrapper(self.fileconfig.getint, option, default,
                                 minval, maxval, note_valid=note_valid)
    def getfloat(self, option, default=sentinel, minval=None, maxval=None,
                 above=None, below=None, note_valid=True):
        return self._get_wrapper(self.fileconfig.getfloat, option, default,
                                 minval, maxval, above, below,
                                 note_valid=note_valid)
    def getboolean(self, option, default=sentinel, note_valid=True):
        return self._get_wrapper(self.fileconfig.getboolean, option, default,
                                 note_valid=note_valid)
    def getchoice(self, option, choices, default=sentinel, note_valid=True):
        if type(choices) == type([]):
            choices = {i: i for i in choices}
        if choices and type(list(choices.keys())[0]) == int:
            c = self.getint(option, default, note_valid=note_valid)
        else:
            c = self.get(option, default, note_valid=note_valid)
        if c not in choices:
            raise error("Choice '%s' for option '%s' in section '%s'"
                        " is not a valid choice" % (c, option, self.section))
        return choices[c]
    def getlists(self, option, default=sentinel, seps=(',',), count=None,
                 parser=str, note_valid=True):
        def lparser(value, pos):
            if len(value.strip()) == 0:
                # Return an empty list instead of [''] for empty string
                parts = []
            else:
                parts = [p.strip() for p in value.split(seps[pos])]
            if pos:
                # Nested list
                return tuple([lparser(p, pos - 1) for p in parts if p])
            res = [parser(p) for p in parts]
            if count is not None and len(res) != count:
                raise error("Option '%s' in section '%s' must have %d elements"
                            % (option, self.section, count))
            return tuple(res)
        def fcparser(section, option):
            return lparser(self.fileconfig.get(section, option), len(seps) - 1)
        return self._get_wrapper(fcparser, option, default,
                                 note_valid=note_valid)
    def getlist(self, option, default=sentinel, sep=',', count=None,
                note_valid=True):
        return self.getlists(option, default, seps=(sep,), count=count,
                             parser=str, note_valid=note_valid)
    def getintlist(self, option, default=sentinel, sep=',', count=None,
                   note_valid=True):
        return self.getlists(option, default, seps=(sep,), count=count,
                             parser=int, note_valid=note_valid)
    def getfloatlist(self, option, default=sentinel, sep=',', count=None,
                     note_valid=True):
        return self.getlists(option, default, seps=(sep,), count=count,
                             parser=float, note_valid=note_valid)
    def getsection(self, section):
        return ConfigWrapper(self.printer, self.fileconfig,
                             self.access_tracking, section)
    def has_section(self, section):
        return self.fileconfig.has_section(section)
    def get_prefix_sections(self, prefix):
        return [self.getsection(s) for s in self.fileconfig.sections()
                if s.startswith(prefix)]
    def get_prefix_options(self, prefix):
        return [o for o in self.fileconfig.options(self.section)
                if o.startswith(prefix)]
    def deprecate(self, option, value=None):
        if not self.fileconfig.has_option(self.section, option):
            return
        if value is None:
            msg = ("Option '%s' in section '%s' is deprecated."
                   % (option, self.section))
        else:
            msg = ("Value '%s' in option '%s' in section '%s' is deprecated."
                   % (value, option, self.section))
        pconfig = self.printer.lookup_object("configfile")
        pconfig.deprecate(self.section, option, value, msg)


######################################################################
# Config file parsing (with include file support)
######################################################################

class ConfigFileReader:
    def __init__(self):
        self.fileconfig_map = {}
    def read_config_file(self, filename):
        try:
            f = open(filename, 'r')
            data = f.read()
            f.close()
        except:
            msg = "Unable to open config file %s" % (filename,)
            raise error(msg)
        return data.replace('\r\n', '\n')
    def build_config_string(self, fileconfig):
        sfile = io.StringIO()
        fileconfig.write(sfile)
        return sfile.getvalue().strip()
    def _append_fileconfig(self, fileconfig, data, filename):
        if not data:
            return
        # Strip trailing comments
        lines = data.split('\n')
        for i, line in enumerate(lines):
            pos = line.find('#')
            if pos >= 0:
                lines[i] = line[:pos]
        sbuffer = io.StringIO('\n'.join(lines))
        if sys.version_info.major >= 3:
            fileconfig.read_file(sbuffer, filename)
        else:
            fileconfig.readfp(sbuffer, filename)
    def append_fileconfig(self, fileconfig, data, filename):
        if filename not in self.fileconfig_map:
            self.fileconfig_map[filename] = self._create_fileconfig()
        self._append_fileconfig(self.fileconfig_map[filename], data, filename)
        self._append_fileconfig(fileconfig, data, filename)
    def _create_fileconfig(self):
        if sys.version_info.major >= 3:
            fileconfig = configparser.RawConfigParser(
                strict=False, inline_comment_prefixes=(';', '#'))
        else:
            fileconfig = configparser.RawConfigParser()
        return fileconfig
    def build_fileconfig(self, data, filename):
        fileconfig = self._create_fileconfig()
        self.append_fileconfig(fileconfig, data, filename)
        return fileconfig
    def _resolve_include(self, source_filename, include_spec, fileconfig,
                         visited):
        dirname = os.path.dirname(source_filename)
        include_spec = include_spec.strip()
        include_glob = os.path.join(dirname, include_spec)
        include_filenames = glob.glob(include_glob)
        if not include_filenames and not glob.has_magic(include_glob):
            # Empty set is OK if wildcard but not for direct file reference
            raise error("Include file '%s' does not exist" % (include_glob,))
        include_filenames.sort()
        for include_filename in include_filenames:
            include_data = self.read_config_file(include_filename)
            self._parse_config(include_data, include_filename, fileconfig,
                               visited)
        return include_filenames
    def _parse_config(self, data, filename, fileconfig, visited):
        path = os.path.abspath(filename)
        if path in visited:
            raise error("Recursive include of config file '%s'" % (filename))
        visited.add(path)
        lines = data.split('\n')
        # Buffer lines between includes and parse as a unit so that overrides
        # in includes apply linearly as they do within a single file
        buf = []
        for line in lines:
            # Strip trailing comment
            pos = line.find('#')
            if pos >= 0:
                line = line[:pos]
            # Process include or buffer line
            mo = configparser.RawConfigParser.SECTCRE.match(line)
            header = mo and mo.group('header')
            if header and header.startswith('include '):
                self.append_fileconfig(fileconfig, '\n'.join(buf), filename)
                del buf[:]
                include_spec = header[8:].strip()
                self._resolve_include(filename, include_spec, fileconfig,
                                      visited)
            else:
                buf.append(line)
        self.append_fileconfig(fileconfig, '\n'.join(buf), filename)
        visited.remove(path)
    def build_fileconfig_with_includes(self, data, filename):
        fileconfig = self._create_fileconfig()
        self._parse_config(data, filename, fileconfig, set())
        return fileconfig

def get_value_position(line, idx):
    start_idx = idx
    while start_idx < len(line) and line[start_idx].isspace():
        start_idx += 1
    if start_idx < len(line):
        pos = {}
        content = line[start_idx:].strip()
        length = len(content)
        end_idx = start_idx + length
        pos["val_start_byte"] = start_idx
        pos["val_end_byte"] = end_idx
        return pos
    return None
def strip_comment(line):
    stripped_str = line.lstrip()
    if not stripped_str or stripped_str[0] not in ('#', ';'):
        return line
    processed_suffix = stripped_str[1:].lstrip()
    return processed_suffix

class ConfigEditor:
    def __init__(self, filename):
        self.cfgrdr = ConfigFileReader()
        data = self.cfgrdr.read_config_file(filename)
        self.fileconfig = self.cfgrdr.build_fileconfig_with_includes(data, filename)
        self.config = ConfigWrapper(None, self.fileconfig, {}, None)
    def get_filename(self, target_section, target_option = None):
        for filename, fileconfig in self.cfgrdr.fileconfig_map.items():
            for section in fileconfig.sections():
                if target_section != section:
                    continue
                if target_option is None:
                    return filename
                for option in fileconfig.options(section):
                    if target_option != option:
                        continue
                    return filename
        return None

    def get_option_val_position(self, filename, target_section, target_option, note_comment_out=False):
        result =  {
            "option_line": -1,
            "val_start_line": -1,
            "val_start_byte": -1,
            "val_end_line": -1,
            "val_end_byte": -1,
        }
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            str_lines = [line.rstrip('\n') for line in lines]

            in_target_section = False
            found_option_key = False
            option_indent = 0

            for line_num, str_line in enumerate(str_lines):
                # original_line = lines[line_num]
                if (note_comment_out):
                    str_line = strip_comment(str_line)
                comment_idx = min(
                    str_line.find('#') if '#' in str_line else len(str_line),
                    str_line.find(';') if ';' in str_line else len(str_line)
                )
                valid_line = str_line[:comment_idx]  # remove comment

                # find seciton
                section_line = valid_line.strip() # remove whitespace character in head and tail
                if section_line.startswith('[') and ']' in section_line:
                    if found_option_key:
                        break
                    in_target_section = section_line[1:section_line.index(']')] == target_section
                    continue

                # not in section, just skip
                if not in_target_section:
                    continue

                if not valid_line.strip():
                    continue
                indent = len(valid_line) - len(valid_line.lstrip())

                if not found_option_key:
                    option_sep_idx = min(
                        valid_line.find(':') if ':' in valid_line else len(valid_line),
                        valid_line.find('=') if '=' in valid_line else len(valid_line)
                    )
                    option_line = valid_line[:option_sep_idx].strip().lower()
                    if option_sep_idx == len(valid_line) or option_line != target_option:
                        continue
                    found_option_key = True
                    option_indent = indent
                    result["option_line"] = line_num

                    pos = get_value_position(valid_line, option_sep_idx + 1)
                    if pos is not None:
                        result["val_start_line"] = result["val_end_line"] = line_num
                        result["val_start_byte"] = pos["val_start_byte"]
                        result["val_end_byte"] = pos["val_end_byte"]
                    continue

                if indent <= option_indent:
                    break
                pos = get_value_position(valid_line, 0)
                if result["val_start_line"] == -1:
                    result["val_start_line"] = line_num
                    result["val_start_byte"] = pos["val_start_byte"]
                result["val_end_line"] = line_num
                result["val_end_byte"] = pos["val_end_byte"]
            return result
        except FileNotFoundError:
            print(f"Error[{filename}]: cannot open file")
            return result
        except Exception as e:
            print(f"Error[{filename}]: {e}")
            return result
