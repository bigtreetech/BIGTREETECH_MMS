#!/bin/bash

KLIPPY_VERSION=0.1.0330 # Important: Keep synced with 'klippy/extras/mms/mms.py'
KLIPPER_SCREEN_VERSION=0.2.0005 # Important: Keep synced with 'KlipperScreen/vivid/installer.py'

SCRIPTNAME="$0"
ARGS=( "$@" )

# Screen Colors
OFF='\033[0m'             # Text Reset
BLACK='\033[0;30m'        # Black
RED='\033[0;31m'          # Red
GREEN='\033[0;32m'        # Green
YELLOW='\033[0;33m'       # Yellow
BLUE='\033[0;34m'         # Blue
PURPLE='\033[0;35m'       # Purple
CYAN='\033[0;36m'         # Cyan
WHITE='\033[0;37m'        # White

B_RED='\033[1;31m'        # Bold Red
B_GREEN='\033[1;32m'      # Bold Green
B_YELLOW='\033[1;33m'     # Bold Yellow
B_CYAN='\033[1;36m'       # Bold Cyan
B_WHITE='\033[1;37m'      # Bold White

TITLE="${B_WHITE}"
DETAIL="${BLUE}"
INFO="${CYAN}"
EMPHASIZE="${B_CYAN}"
ERROR="${B_RED}"
WARNING="${B_YELLOW}"
PROMPT="${CYAN}"
DIM="${PURPLE}"
INPUT="${OFF}"
SECTION="----------------\n"

SHELL_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/ && pwd )"

KLIPPER_HOME="${HOME}/klipper"
KLIPPER_CONFIG_HOME="${HOME}/printer_data/config"

EXTRAS_DIR="klippy/extras"
extras_src_dir="${SHELL_DIR}/${EXTRAS_DIR}"
extras_dst_dir="${KLIPPER_HOME}/${EXTRAS_DIR}"
neopixel="$extras_dst_dir/neopixel.py"
aht30="$extras_dst_dir/aht10.py"

KS_DIR="KlipperScreen"
VIVID_DIR="${KS_DIR}/vivid"
ks_dir="${HOME}/${KS_DIR}"
ks_src_dir="${SHELL_DIR}/${VIVID_DIR}"
ks_dst_dir="${HOME}/${VIVID_DIR}"
screen="$ks_dir/screen.py"
gcodes="$ks_dir/panels/gcodes.py"

g_vivid_id=""
g_buffer_id=""
g_cutter=0
g_entry_sensor=0
g_trash_can=0
g_brush=0
g_aht30_patch=0
g_klippe_screen=0

# The oldest version supported
klipper_oldest_id="938300f3c3cc25448c499a3a8ca5b47b7a6d4fa8"
# The latest version supported. usually is the latest version of the upstream
klipper_latest_id="9c84895a09fa408b2838ce85a2540ee7d4eeb117"
# Klipper supports AHT30 and no longer requires patching after this commit
aht30_patch_id="1f43be0b8b55d90753578d06ac06356d1ab9a768"
# The oldest version supported
ks_oldest_id="b3115f9b9b329642d4dbf0ad225ab065ea3eda80"
# The latest version supported. usually is the latest version of the upstream
ks_latest_id="61f7afd1e21f7b022e7a6bfb29992d3c396a5c50"

function nextfilename {
    local name="$1"
    if [ -d "${name}" ]; then
        printf "%s-%s" ${name} $(date '+%Y%m%d_%H%M%S')
    else
        printf "%s-%s.%s-old" ${name%.*} $(date '+%Y%m%d_%H%M%S') ${name##*.}
    fi
}

prompt_yn() {
    while true; do
        read -n1 -p "$@ (y/n)? " yn
        case "${yn}" in
            Y|y)
                echo -n "y"
                break
                ;;
            N|n)
                echo -n "n"
                break
                ;;
            *)
                ;;
        esac
    done
}

prompt_123() {
    prompt=$1
    max=$2
    while true; do
        if [ -z "${max}" ]; then
            read -ep "${prompt}? " number
        elif [[ "${max}" -lt 10 ]]; then
            read -ep "${prompt} (1-${max})? " -n1 number
        else
            read -ep "${prompt} (1-${max})? " number
        fi
        if ! [[ "$number" =~ ^-?[0-9]+$ ]] ; then
            echo -e "Invalid value." >&2
            continue
        fi
        if [ "$number" -lt 1 ]; then
            echo -e "Value must be greater than 0." >&2
            continue
        fi
        if [ -n "$max" ] && [ "$number" -gt "$max" ]; then
            echo -e "Value must be less than $((max+1))." >&2
            continue
        fi
        echo ${number}
        break
    done
}

prompt_option() {
    local var_name="$1"
    local query="$2"
    shift 2
    local i=0
    for val in "$@"; do
        i=$((i+1))
        echo "$i) $val"
    done
    REPLY=$(prompt_123 "$query" "$#")
    declare -g $var_name="${!REPLY}"
}
option() {
    local var_name="$1"
    local desc="$2"
    declare -g $var_name="${desc}"
    OPTIONS+=("$desc")
}
option_del() {
    local desc="$1"
    local new_opts=()
    for v in "${OPTIONS[@]}"; do
        [[ "$v" == "$desc" ]] && continue
        new_opts+=("$v")
    done
    OPTIONS=("${new_opts[@]}")
}

abort(){
    if [ ! $# -eq 0 ]; then
        echo -e "${ERROR}$1${INPUT}"
    fi
    echo -e "${ERROR}Installation has been aborted!${INPUT}"
    exit -1
}

self_update() {
    [ "$UPDATE_GUARD" ] && return
    export UPDATE_GUARD=YES
    clear

    cd "$SCRIPTPATH"

    set +e
    # There is no timeout function provided in the system
    if [ -n "$(which timeout)" ]; then
        BRANCH=$(timeout 3s git branch --show-current)
    else
        BRANCH=$(git branch --show-current)
    fi

    if [ $? -ne 0 ]; then
        echo -e "${ERROR}Error updating from github"
        echo -e "${ERROR}You might have an old version of git"
        echo -e "${ERROR}Skipping automatic update..."
        set -e
        return
    fi
    set -e

    [ -z "${BRANCH}" ] && {
        echo -e "${ERROR}Timeout talking to github. Skipping upgrade check"
        return
    }
    echo -e "${B_GREEN}Running on '${BRANCH}' branch"

    # Both check for updates but also help me not loose changes accidently
    echo -e "${B_GREEN}Checking for updates..."
    git fetch --quiet

    set +e
    git diff --quiet --exit-code "origin/$BRANCH"
    if [ $? -eq 1 ]; then
        echo -e "${B_GREEN}Found a new version of BIGTREETECH_ViViD on github, updating..."
        [ -n "$(git status --porcelain)" ] && {
            git stash push -m 'local changes stashed before self update' --quiet
        }
        RESTART=1
    fi
    set -e

    if [ -n "${RESTART}" ]; then
        git checkout $BRANCH --quiet
        if git symbolic-ref -q HEAD > /dev/null; then
            # On a branch (if using tags we will be detached)
            git pull --quiet --force origin $BRANCH
        fi
        GIT_VER=$(git describe --tags)
        echo -e "${B_GREEN}Now on git version ${GIT_VER}"
        echo -e "${B_GREEN}Running the new install script..."
        cd - >/dev/null
        exec "$SCRIPTNAME" "${ARGS[@]}"
        exit 0 weg# Exit this old instance
    fi
    GIT_VER=$(git describe --tags)
    echo -e "${B_GREEN}Already the latest version: ${GIT_VER}"
}

verify_not_root() {
    if [[ $EUID -eq 0 || -n "${SUDO_USER}" ]]; then
        abort "Cannot install with sudo (root) privileges"
    fi
}

verify_version() {
    local name=$1
    local dir=$2
    local oldest=$3
    local latest=$4

    if [ -d "${dir}" ]; then
        local commit_id=$(git -C "${dir}" log -n 1 --pretty=%H)
        local err_oldest=""
        local err_lastest=""

        if ! git -C "${dir}" merge-base --is-ancestor "${oldest}" "${commit_id}"; then
            err_oldest="Too ${PURPLE}old${WARNING} version "
        fi

        if git -C "${dir}" cat-file -e "${latest}" 2>/dev/null; then
            if ! git -C "${dir}" merge-base --is-ancestor "${commit_id}" "${latest}"; then
                err_lastest="Too ${PURPLE}new${WARNING} version "
            fi
        fi

        if [ ! -z "${err_oldest}" ] || [ ! -z "${err_lastest}" ]; then
            local commit_id=$(git -C ${dir} describe --tags)
            echo -e "${WARNING}${SECTION}Your ${name} version is: ${PURPLE}${commit_id}${WARNING}
not between ${PURPLE}${oldest}${WARNING} and ${PURPLE}${latest}${WARNING}
${err_oldest}${err_lastest}may not be suitable, it is best to update ${name} version as suggested.${INPUT}"
            yn=$(prompt_yn "I confirm that this version of ${name} is compatible with ViViD.")
            echo
            if [ "$yn" = "n" ]; then
                abort "${name} version ${PURPLE}${commit_id}${ERROR} seems incompatible."
            fi
        fi
    fi
}

verify_home_dirs() {
    if [ -d "${KLIPPER_HOME}" ]; then
        verify_version "Klipper" "${KLIPPER_HOME}" "${klipper_oldest_id}" "${klipper_latest_id}"

        local commit_id=$(git -C "${KLIPPER_HOME}" log -n 1 --pretty=%H)
        if ! git -C "${KLIPPER_HOME}" merge-base --is-ancestor "${aht30_patch_id}" "${commit_id}"; then
            g_aht30_patch=1
        fi
    else
        echo -e "${ERROR}Klipper home directory (${PURPLE}${KLIPPER_HOME}${ERROR}) not found."
        abort
    fi

    if [ ! -d "${KLIPPER_CONFIG_HOME}" ]; then
        echo -e "${ERROR}Klipper config directory (${PURPLE}${KLIPPER_CONFIG_HOME}${ERROR}) not found."
        abort
    fi

    if [ -d "${ks_dir}" ]; then
        verify_version "KlipperScreen" "${ks_dir}" "${ks_oldest_id}" "${ks_latest_id}"
    fi
}

set_serial_id() {
    mapfile -t OPTIONS < <(ls /dev/serial/by-id/ 2>/dev/null)
    local opt_num=${#OPTIONS[@]}
    if [ "${opt_num}" == 0 ]; then
        echo -e "${WARNING}${SECTION}Device serial id not found, please confirm if the ViViD cable is properly plugged in.${INPUT}"
        yn=$(prompt_yn "Do not configure the serial id for now, manually modify it after installation is complete.")
        echo
        if [ "$yn" = "n" ]; then
            abort
        fi
    else
        if [ "${opt_num}" == 1 ]; then
            echo -e "${WARNING}${SECTION}Only 1 serial id was found. ViViD requires at least 2(ViViD + buffer). Please confirm if the ViViD cable is properly plugged in.${INPUT}"
            yn=$(prompt_yn "Configure one first, and manually modify the rest after installation is complete.")
            echo
            if [ "$yn" = "n" ]; then
                abort
            fi
        fi

        option NONE "Don't configure it yet"

        opt_num=${#OPTIONS[@]}
        if [ "${opt_num}" -gt 1 ]; then
            echo -e "${PROMPT}${SECTION}Please select one of the IDs from the list below as the ID for ${PURPLE}ViViD${PROMPT}.${INPUT}"
            prompt_option opt "ViViD MCU serial id:" "${OPTIONS[@]}"
            if [ "${opt}" != "${NONE}" ]; then
                option_del "${opt}"
                g_vivid_id=${opt}
                echo -e "${PROMPT}ViViD MCU serial id: ${PURPLE}${g_vivid_id}${INPUT}"
            fi
        fi

        opt_num=${#OPTIONS[@]}
        if [ "${opt_num}" -gt 1 ]; then
            echo -e "${PROMPT}${SECTION}Please select one of the IDs from the list below as the ID for ${PURPLE}Buffer${PROMPT}.${INPUT}"
            prompt_option opt "Buffer MCU serial id:" "${OPTIONS[@]}"
            if [ "${opt}" != "${NONE}" ]; then
                option_del "${opt}"
                g_buffer_id=${opt}
                echo -e "${PROMPT}Buffer MCU serial id: ${PURPLE}${g_buffer_id}${INPUT}"
            fi
        fi
    fi
}

set_cutter() {
    # Cutter
    echo -e "${PROMPT}${SECTION}Installing the ${PURPLE}Cutter${PROMPT} is crucial!${INPUT}"
    yn=$(prompt_yn "Has the cutter been installed?")
    echo
    if [ "$yn" = "n" ]; then
        echo -e "${ERROR}Installing a cutter is crucial, printing multi-colors may damage the ViViD and the printer without a cutter.${INPUT}"
        yn=$(prompt_yn "Continue first, the cutter will be installed later.")
        echo
        if [ "$yn" = "n" ]; then
            abort "ViViD cannot be used without a cutter!"
        fi
    fi
    g_cutter=1
}

set_entry_sensor() {
    # Entry sensor
    echo -e "${PROMPT}${SECTION}Installing an ${PURPLE}Entry Sensor${PROMPT} in toolhead can improve the accuracy of ViViD in identifying the location of filament!${INPUT}"
    yn=$(prompt_yn "Has the entry sensor been installed?")
    echo
    if [ "$yn" = "n" ]; then
        echo -e "${WARNING}Installing an entry sensor is highly recommended, as it can improve the accuracy of ViViD in identifying the location of filament.${INPUT}"
        yn=$(prompt_yn "Do you still want to continue without entry sensor?")
        echo
        if [ "$yn" = "n" ]; then
            abort
        fi
        g_entry_sensor=0
    else
        g_entry_sensor=1
    fi
}

set_trash_can() {
    # Trash can
    echo -e "${PROMPT}${SECTION}If a ${PURPLE}Trash Can${PROMPT} is installed, the old filament can be quickly purged into the trash can when loading new filament.${INPUT}"
    yn=$(prompt_yn "Has the trash can been installed?")
    echo
    if [ "$yn" = "n" ]; then
        g_trash_can=0
    else
        g_trash_can=1
    fi
}

set_brush() {
    # Brush
    echo -e "${PROMPT}${SECTION}If a ${PURPLE}Brush${PROMPT} is installed, it can clean up scrap stuck to the nozzle with a brush before start/resume printing.${INPUT}"
    yn=$(prompt_yn "Has the brush been installed?")
    echo
    if [ "$yn" = "n" ]; then
        g_brush=0
    else
        g_brush=1
    fi
}

set_klipper_screen() {
    # KlipperScreen
    echo -e "${PROMPT}${SECTION}Installing ${PURPLE}KlipperScreen for ViViD${PROMPT} will add a ViViD management menu to KlipperScreen.${INPUT}"
    yn=$(prompt_yn "Install KlipperScreen?")
    echo
    if [ "$yn" = "n" ]; then
        g_klippe_screen=0
    else
        g_klippe_screen=1
    fi
}

get_version() {
    VIVID_VER=$(git describe --tags)
    echo -e "${B_GREEN}${SECTION}ViViD script: ${VIVID_VER}"
    echo -e "Klipper: ${KLIPPY_VERSION}"
    echo -e "KlipperScreen: ${KLIPPER_SCREEN_VERSION}${INPUT}"
}

install_klippy() {
    # install klipper
    echo -e "${INFO}${SECTION}Installing ViViD to Klipper: ${PURPLE}${KLIPPY_VERSION}"
    if [ -d "${extras_dst_dir}" ]; then
        # link extras/mms/*.py
        # cd to ViViD source code path
        cd "${extras_src_dir}"
        # traverse directory
        find . -type d | while IFS= read -r tmp_dir; do
            # remove '.'
            tmp_dir=${tmp_dir#.}
            # get source & destination path
            src_dir="${extras_src_dir}${tmp_dir}"
            dst_dir="${extras_dst_dir}${tmp_dir}"
            # create the same extension directory in klipper path
            mkdir -p "${dst_dir}"
            # ban the null value, such as: don't set "*.py" to the value of 'file'
            shopt -s nullglob
            for file in ${src_dir}/*.py; do
                ln -sf "${file}" "${dst_dir}/$(basename "${file}")"
            done
            shopt -u nullglob
        done
        echo -e "${INFO}ViViD ${PURPLE}${extras_dst_dir}${INFO} link completed!"
        # patch neopixel
        if [[ -f "${neopixel}" ]]; then
            sed -i '/^BIT_MAX_TIME=.000004/ { s/^/# /; a\
BIT_MAX_TIME=.000030
            }' "${neopixel}"
            echo -e "${INFO}ViViD ${PURPLE}${neopixel}${INFO} patch completed!"
        else
            echo -e "${WARNING}ViViD ${PURPLE}${neopixel}${INFO} not patched!"
        fi

        # patch aht30
        if [ "${g_aht30_patch}" == 1 ]; then
            if [[ -f "${aht30}" ]]; then
                sed -i '/^[[:space:]]*'"'"'INIT'"'"'[[:space:]]*:[[:space:]]*\[0xE1, 0x08, 0x00\],/ { s/^/# /; a\
        '"'"'INIT'"'"'              :[0xBE, 0x08, 0x00],
                }' "${aht30}"
                echo -e "${INFO}ViViD ${PURPLE}${aht30}${INFO} patch completed!"
            else
                echo -e "${WARNING}ViViD ${PURPLE}${aht30}${INFO} not patched!"
            fi
            local aht30_cfg="${KLIPPER_CONFIG_HOME}/sample-bigtreetech-mms/hardware/mms-heater.cfg"
            sed -i 's/sensor_type: AHT3X/sensor_type: AHT10/g' "${aht30_cfg}"
        fi

        echo -e "${INFO}ViViD for klipper installation completed!"
    else
        abort "ViViD not installed because ${PURPLE}${extras_dst_dir}${ERROR} directory not found!"
    fi
}

uninstall_klippy() {
    # uninstall klipper
    echo -e "${INFO}${SECTION}Unlinking ViViD from Klipper..."
    if [ -d "${extras_dst_dir}" ]; then
        # unlink extras/mms/*.py
        rm -rf "${extras_dst_dir}/mms"
        echo -e "${INFO}ViViD ${PURPLE}${extras_dst_dir}${INFO} removed!"
        # unpatch neopixel
        if [[ -f "${neopixel}" ]]; then
            sed -i 's/^[[:space:]]*#[[:space:]]*BIT_MAX_TIME=.000004/BIT_MAX_TIME=.000004/' "${neopixel}"
            sed -i '/^BIT_MAX_TIME=.000030/d' "${neopixel}"
            echo -e "${INFO}ViViD ${PURPLE}${neopixel}${INFO} unpatched!"
        fi
        # unpatch aht30
        if [ "${g_aht30_patch}" == 1 ]; then
            if [[ -f "${aht30}" ]]; then
                sed -i '/^#\s*'\''INIT'\''\s*:\[0xE1, 0x08, 0x00\],$/ s/^# //'  "${aht30}"
                sed -i '/^[[:space:]]*'\''INIT'\''[[:space:]]*:[[:space:]]*\[0xBE, 0x08, 0x00\],$/d' "${aht30}"
                echo -e "${INFO}ViViD ${PURPLE}${aht30}${INFO} unpatched!"
            fi
        fi
        echo -e "${INFO}ViViD for klipper uninstallation completed!"
    else
        abort "ViViD not uninstalled because ${PURPLE}${extras_dst_dir}${ERROR} directory not found!"
    fi
}

copy_config_files() {
    local dst_dir="${KLIPPER_CONFIG_HOME}/sample-bigtreetech-mms"
    local next_dst_dir="$(nextfilename "${dst_dir}")"
    local src_dir="${SHELL_DIR}/config/sample-bigtreetech-mms"

    if [ -d "${dst_dir}" ]; then
        mv "${dst_dir}" "${next_dst_dir}"
        echo -e "${INFO}Old config backup completed: ${PURPLE}${next_dst_dir}${INPUT}"
    fi
    echo -e "${INFO}Copying ${PURPLE}${src_dir}${INFO} into ${PURPLE}${dst_dir}${INFO} directory...${INPUT}"
    cp -r "${src_dir}" "${dst_dir}"
}

include_exclude_config_files() {
    local include=$1
    local printer_cfg="${KLIPPER_CONFIG_HOME}/printer.cfg"
    local mms_sed='\[include sample-bigtreetech-mms/mms.cfg\]'
    if [ -f "${printer_cfg}" ]; then
        if [ "${include}" -eq 0 ]; then
            sed -i -e "\|${mms_sed}|d" "$printer_cfg"
            echo -e "${INFO}ViViD config has been removed from ${PURPLE}${printer_cfg}"
        else
            local already_included=$(grep -c "${mms_sed}" ${printer_cfg} || true)
            if [ "${already_included}" -eq 0 ]; then
                sed -i "1i ${mms_sed}" ${printer_cfg}
                echo -e "${INFO}ViViD config has been added to ${PURPLE}${printer_cfg}"
            else
                echo -e "${INFO}ViViD config already exists in ${PURPLE}${printer_cfg}${INFO} there is no need to add it again"
            fi
        fi
    else
        echo -e "${WARNING}Klipper config file ${PURPLE}${printer_cfg}${WARNING} not found!"
    fi
}

unpatch_KlipperScreen() {
    # unpatch screen.py
    if [[ -f "${screen}" ]]; then
        sed -i -e '/^[[:space:]]*from[[:space:]]\+vivid\.installer[[:space:]]\+import[[:space:]]\+install_vivid[[:space:]]*$/d' \
                -e '/^[[:space:]]*install_vivid(self\.base_panel)[[:space:]]*$/d' \
                -e '/^[[:space:]]*requested_updates\['\''objects'\''\]\["mms"\][[:space:]]*=[[:space:]]*\["slots", "steppers", "buffers"\][[:space:]]*$/d' \
                "${screen}"
    fi
    # unpatch panels/gcodes.py
    if [[ -f "${gcodes}" ]]; then
        sed -i "\|^[[:space:]]*{\"name\": \"ViViD\", \"response\": 444, \"style\": 'dialog-info'},$|d" "${gcodes}"
        start_line=$(grep -n -F -m1 'response_id == 444' "${gcodes}" | cut -d: -f1)
        end_line=$(grep -n -F -m1 'parent_hook = self.confirm_print,' "${gcodes}" | cut -d: -f1)
        if [ "$start_line" ] && [ "$end_line" ]; then
            delete_end=$((end_line + 1))
            sed -i "${start_line},${delete_end}d" "${gcodes}"
        fi
    fi
}

install_KlipperScreen() {
    # install KlipperScreen
    echo -e "${INFO}${SECTION}Installing ViViD to KlipperScreen ${PURPLE}${KLIPPER_SCREEN_VERSION}"
    if [ -d "${ks_dir}" ]; then
        # link KlipperScreen/vivid/*.{py,css,svg}
        # cd to ViViD source code path
        cd "${ks_src_dir}"
        # traverse directory
        find . -type d | while IFS= read -r tmp_dir; do
            # remove '.'
            tmp_dir=${tmp_dir#.}
            # get source & destination path
            src_dir="${ks_src_dir}${tmp_dir}"
            dst_dir="${ks_dst_dir}${tmp_dir}"
            # create the same extension directory in KlipperScreen path
            mkdir -p "${dst_dir}"
            # ban the null value, such as: don't set "*.py" to the value of 'file'
            shopt -s nullglob
            for file in ${src_dir}/*.{py,css,svg}; do
                ln -sf "${file}" "${dst_dir}/$(basename "${file}")"
            done
            shopt -u nullglob
        done

        echo -e "${INFO}ViViD ${PURPLE}${ks_dst_dir}${INFO} link completed!"

        # unpatch, avoid duplicate patches
        unpatch_KlipperScreen

        # patch screen.py
        if [[ -f "${screen}" ]]; then
            sed -i '/^from[[:space:]]\+panels\.base_panel[[:space:]]\+import[[:space:]]\+BasePanel$/a\
from vivid.installer import install_vivid' "${screen}"
            sed -i '/^[[:space:]]*self\.base_panel\.activate()[[:space:]]*$/i\
        install_vivid(self.base_panel)' "$screen"
            sed -i '/^[[:space:]]*self\._ws\.klippy\.object_subscription(requested_updates)[[:space:]]*$/i\
        requested_updates['\''objects'\'']["mms"] = ["slots", "steppers", "buffers"]' "${screen}"

            echo -e "${INFO}ViViD KlipperScreen ${PURPLE}${screen}${INFO} patch completed!"
        else
            echo -e "${WARNING}ViViD KlipperScreen ${PURPLE}${screen}${WARNING} not patched!"
        fi

        # patch gcodes
        if [[ -f "${gcodes}" ]]; then
            new_gcode=$(mktemp)
cat >"$new_gcode" <<'EOF'
        elif response_id == 444:
            # "RESPONSETYPE_VIVID" -> 444
            # Get UUID as panel unique mark for panel
            fileinfo = self._screen.files.get_file_info(filename)
            file_uuid = fileinfo.get("uuid", None)
            unq_remark = file_uuid or filename

            # For ViViD
            self._screen.show_panel(
                "vivid/panels/preprint",
                # Use an unique_panel_name here,
                # panel_name is the cache key of KlipperScreen's show_panel()
                panel_name = f"vivid/preprint/${unq_remark}",
                gcode_file = filename,
                parent_hook = self.confirm_print,
            )
EOF

            sed -i '/^[[:space:]]*buttons[[:space:]]*=[[:space:]]*\[/a\
            {"name": "ViViD", "response": 444, "style": '\''dialog-info'\''},' "${gcodes}"

            sed -i '\|^[[:space:]]*self.confirm_delete_file(None,[[:space:]]*f"gcodes/{filename}")[[:space:]]*$|r '"${new_gcode}"'' "${gcodes}"
            rm -f "$new_gcode"
            echo -e "${INFO}ViViD KlipperScreen ${PURPLE}${gcodes}${INFO} patch completed!"
        else
            echo -e "${WARNING}ViViD KlipperScreen ${PURPLE}${gcodes}${WARNING} not patched!"
        fi
    else
        abort "ViViD KlipperScreen not installed because ${PURPLE}${ks_dir}${ERROR} directory not found!"
    fi
}

uninstall_KlipperScreen() {
    # uninstall klipper
    echo -e "${INFO}${SECTION}Unlinking ViViD from KlipperScreen..."
    if [ -d "${ks_dir}" ]; then
        # unlink KlipperScreen/vivid/*.{py,css,svg}
        rm -rf "${ks_dst_dir}"
        echo -e "${INFO}ViViD ${PURPLE}${ks_dst_dir}${INFO} removed!"

        # unpatch screen.py & panels/gcodes.py
        unpatch_KlipperScreen
        echo -e "${INFO}ViViD ${PURPLE}${screen}&${gcodes}${INFO} unpatched!"

        echo -e "${INFO}ViViD for klipper uninstallation completed!"
    else
        abort "ViViD KlipperScreen not uninstalled because ${PURPLE}${ks_dir}${ERROR} directory not found!"
    fi
}

set_user_config() {
    local mms_dir="${KLIPPER_CONFIG_HOME}/sample-bigtreetech-mms"
    local mms_path="${mms_dir}/mms.cfg"
    local swap_dir="${mms_dir}/swap"
    local purge="${swap_dir}/mms-purge.cfg"
    local brush="${swap_dir}/mms-brush.cfg"
    local cutter="${swap_dir}/mms-cut.cfg"

    echo -e "${INFO}Cutter must be configured, please configure the specific position in ${PURPLE}${cutter}${INPUT}"

    # entry sensor
    if [ "${g_entry_sensor}" -eq 1 ]; then
        sed -i -e "s|^#\s*\(entry_sensor: EBBCan:gpio21\)|\1|" "${mms_path}"
        echo -e "${INFO}Entry Sensor has been enabled, please configure the specific pin in ${PURPLE}${mms_path}${INPUT}"
    fi

    # trash can
    if [ "${g_trash_can}" -eq 0 ]; then
        sed -i -e "s|enable: 1|enable: 0|g" "${purge}"
    else
        echo -e "${INFO}Trash can has been enabled, please configure the specific position in ${PURPLE}${purge}${INPUT}"
    fi

    # brush
    if [ "${g_brush}" -eq 0 ]; then
        sed -i -e "s|enable: 1|enable: 0|g" "${brush}"
    else
        echo -e "${INFO}Brush has been enabled, please configure the specific position in ${PURPLE}${brush}${INPUT}"
    fi

    # vivid seral id
    if [ -z "${g_vivid_id}" ]; then
        echo -e "${WARNING}ViViD MCU serial id has not been set. Please modify it manually in ${PURPLE}${mms_dir}/mms.cfg${INPUT}"
    else
        echo -e "${INFO}ViViD Serial ID: ${PURPLE}${g_vivid_id}${INPUT}"
        sed -i "s|usb-Klipper_stm32g0b1xx_vivid-if00|${g_vivid_id}|g" "${mms_dir}/mms.cfg"
    fi
    # buffer seral id
    if [ -z "${g_buffer_id}" ]; then
        echo -e "${WARNING}Buffer MCU serial id has not been set. Please modify it manually in ${PURPLE}${mms_dir}/mms.cfg${INPUT}"
    else
        echo -e "${INFO}Buffer Serial ID: ${PURPLE}${g_buffer_id}${INPUT}"
        sed -i "s|usb-Klipper_stm32f042x6_buffer-if00|${g_buffer_id}|g" "${mms_dir}/mms.cfg"
    fi
}

install_vivid() {
    set_serial_id
    set_cutter
    set_entry_sensor
    set_trash_can
    set_brush

    install_klippy

    # vivid config files
    copy_config_files
    set_user_config
    # include in printer.cfg
    include_exclude_config_files 1

    echo -e "${INPUT}"
    yn=$(prompt_yn "Klipper has been installed. Restart immediately? (This will interrupt printing if there are any ongoing tasks.)")
    echo
    if [ "$yn" = "y" ]; then
        sudo systemctl restart klipper
        echo -e "${INFO}The Klipper service has been restarted.${INPUT}"
    else
        echo -e "${WARNING}The Klipper service needs to be restarted for it to take effect. Please manually restart it later.${INPUT}"
    fi

    set_klipper_screen
    if [ "${g_klippe_screen}" -eq 1 ]; then
        install_KlipperScreen

        echo -e "${INPUT}"
        yn=$(prompt_yn "KlipperScreen has been installed. Restart immediately? (This may interrupt printing if there are any ongoing tasks.)")
        echo
        if [ "$yn" = "y" ]; then
            sudo systemctl restart KlipperScreen.service
            echo -e "${INFO}The KlipperScreen service has been restarted.${INPUT}"
        else
            echo -e "${WARNING}The KlipperScreen service needs to be restarted for it to take effect. Please manually restart it later.${INPUT}"
        fi
    fi

    echo -e "${GREEN}${SECTION}ViViD MCU serial id: ${g_vivid_id}"
    echo -e "Buffer MCU serial id: ${g_buffer_id}"
    echo -e "Cutter: ${g_cutter}"
    echo -e "Entry Sensor: ${g_entry_sensor}"
    echo -e "Trash Can: ${g_trash_can}"
    echo -e "Brush: ${g_brush}"
    echo -e "KlipperScreen: ${g_klippe_screen}"

    get_version

    echo -e "${GREEN}${SECTION}ViViD installation is complete.${INPUT}"
}

uninstall_vivid() {
    uninstall_klippy
    uninstall_KlipperScreen
    # exclude in printer.cfg
    include_exclude_config_files 0

    echo -e "${INPUT}"

    yn=$(prompt_yn "Klipper has been uninstalled. Restart immediately? (This will interrupt printing if there are any ongoing tasks.)")
    echo
    if [ "$yn" = "y" ]; then
        sudo systemctl restart klipper
        echo -e "${INFO}The Klipper service has been restarted.${INPUT}"
    else
        echo -e "${WARNING}The Klipper service needs to be restarted for it to take effect. Please manually restart it later.${INPUT}"
    fi

    yn=$(prompt_yn "KlipperScreen has been installed. Restart immediately? (This may interrupt printing if there are any ongoing tasks.)")
    echo
        if [ "$yn" = "y" ]; then
            sudo systemctl restart KlipperScreen.service
            echo -e "${INFO}The KlipperScreen service has been restarted.${INPUT}"
        else
            echo -e "${WARNING}The KlipperScreen service needs to be restarted for it to take effect. Please manually restart it later.${INPUT}"
        fi

    echo -e "${GREEN}${SECTION}ViViD uninstallation is complete.${INPUT}"
}

usage() {
    echo -e "${EMPHASIZE}"
    echo "Usage: $0 [-h] [-i] [-d] [-z] [-g]"
    echo
    echo "-h for help"
    echo "-i for install"
    echo "-d for uninstall"
    echo "-z skip github update check"
    echo "-g for get version"
    echo "(no flags for default -i install)"
    echo
    abort
}

# step 1
while getopts "idzgh" arg; do
    case $arg in
        i) INSTALL=1;;
        d) UNINSTALL=1;;
        z) SKIP_UPDATE=1;;
        g) GET_VERSION=1;;
        h) usage;;
        *) usage;;
    esac
done

if [ "${GET_VERSION}" == 1 ]; then
    get_version
    exit 0
fi

# step 2: check
if [ "${INSTALL}" == 1 ] && [ "${UNINSTALL}" == 1 ]; then
    echo -e "${ERROR}Can't install and uninstall at the same time!"
    usage
fi

# step 3: check
verify_not_root
[ -z "${SKIP_UPDATE}" ] && {
    self_update # Make sure the repo is up-to-date on correct branch
}
verify_home_dirs

# step 4: running
if [ "${INSTALL}" == 1 ]; then
    install_vivid
elif [ "${UNINSTALL}" == 1 ]; then
    uninstall_vivid
else
    install_vivid # default install
fi

# step 5: end
echo -e "${INPUT}"
