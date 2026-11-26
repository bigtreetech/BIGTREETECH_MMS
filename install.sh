#!/bin/bash

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
SECTION="\n----------------"

SHELL_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/ && pwd )"

source_version() {
    local version
    version=$(sed -n -E 's/^[[:space:]]*version:[[:space:]]*str[[:space:]]*=[[:space:]]*"([0-9.]+)".*/\1/p' "$1")
    printf '%s\n' "${version:-unknown}"
}

KLIPPY_VERSION=$(source_version "${SHELL_DIR}/klippy/extras/mms/mms.py")
KLIPPER_SCREEN_VERSION=$(source_version "${SHELL_DIR}/KlipperScreen/vivid/installer.py")

KLIPPER_HOME="${HOME}/klipper"
KLIPPER_CONFIG_HOME="${HOME}/printer_data/config"

EXTRAS_DIR="klippy/extras"
extras_src_dir="${SHELL_DIR}/${EXTRAS_DIR}"
extras_dst_dir="${KLIPPER_HOME}/${EXTRAS_DIR}"
neopixel="$extras_dst_dir/neopixel.py"
aht30="$extras_dst_dir/aht10.py"

MOONRAKER_HOME="${HOME}/moonraker"
MOONRAKER_COMPONENTS_DIR="${MOONRAKER_HOME}/moonraker/components"
KS_DIR="KlipperScreen"
VIVID_DIR="${KS_DIR}/vivid"
ks_dir="${HOME}/${KS_DIR}"
ks_src_dir="${SHELL_DIR}/${VIVID_DIR}"
ks_dst_dir="${HOME}/${VIVID_DIR}"
screen="$ks_dir/screen.py"
gcodes="$ks_dir/panels/gcodes.py"
ks_activate_pattern='^        self\.base_panel\.activate\(\)[[:space:]]*$'

g_mms_dir="${KLIPPER_CONFIG_HOME}/bigtreetech-mms"
g_mms_path="${g_mms_dir}/mms/mms.cfg"
g_old_config_path=""
g_vivid_id=""
g_buffer_id=""
g_cutter=0
g_entry_sensor=0
g_purge_brush=0
g_aht30_patch=0
g_klippe_screen=0
g_log_en=1

# The oldest version supported
klipper_oldest_id="938300f3c3cc25448c499a3a8ca5b47b7a6d4fa8"
# Upstream baseline used for candidate verification.
klipper_latest_id="7558e8c2f6f3f2ca4b5ff1ddd85d2b82f0c3881d"
# Klipper supports AHT30 and no longer requires patching after this commit
aht30_patch_id="1f43be0b8b55d90753578d06ac06356d1ab9a768"
# The oldest version supported
ks_oldest_id="b3115f9b9b329642d4dbf0ad225ab065ea3eda80"
# Upstream baseline used for candidate verification.
ks_latest_id="22604c770ce199edd057c095680ec8751184765e"

log_enable() {
    g_log_en=1
}

log_disable() {
    g_log_en=0
}

log_echo() {
    if [ "${g_log_en}" -eq 0 ]; then
        return
    fi
    log_text=${1}
    echo -e "${log_text}${INPUT}"
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

abort(){
    if [ ! $# -eq 0 ]; then
        log_echo "${ERROR}$1"
    fi
    log_echo "${ERROR}Installation has been aborted!"
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
        log_echo "${ERROR}Error updating from github"
        log_echo "${ERROR}You might have an old version of git"
        log_echo "${ERROR}Skipping automatic update..."
        set -e
        return
    fi
    set -e

    [ -z "${BRANCH}" ] && {
        log_echo "${ERROR}Timeout talking to github. Skipping upgrade check"
        return
    }
    log_echo "${B_GREEN}Running on '${BRANCH}' branch"

    # Both check for updates but also help me not loose changes accidently
    log_echo "${B_GREEN}Checking for updates..."
    git fetch --quiet

    set +e
    git diff --quiet --exit-code "origin/$BRANCH"
    if [ $? -eq 1 ]; then
        log_echo "${B_GREEN}Found a new version of BIGTREETECH_MMS on github, updating..."
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
        GIT_VER=$(git describe --tags --always)
        log_echo "${B_GREEN}Now on git version ${GIT_VER}"
        log_echo "${B_GREEN}Running the new install script..."
        cd - >/dev/null
        exec "$SCRIPTNAME" "${ARGS[@]}"
        exit 0 weg# Exit this old instance
    fi
    GIT_VER=$(git describe --tags --always)
    log_echo "${B_GREEN}Already the latest version: ${GIT_VER}"
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

    if [ -d "${dir}" ] && [ "${UNINSTALL}" != 1 ]; then
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
            local commit_id=$(git -C "${dir}" describe --tags --always)
            log_echo "${TITLE}${SECTION}"
            log_echo "${WARNING}Your ${name} version is: ${PURPLE}${commit_id}${WARNING}
outside the verified baselines ${PURPLE}${oldest}${WARNING} and ${PURPLE}${latest}${WARNING}
${err_oldest}${err_lastest}may not be suitable, it is best to update ${name} version as suggested."
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
        log_echo "${ERROR}Klipper home directory (${PURPLE}${KLIPPER_HOME}${ERROR}) not found."
        abort
    fi

    if [ ! -d "${KLIPPER_CONFIG_HOME}" ]; then
        log_echo "${ERROR}Klipper config directory (${PURPLE}${KLIPPER_CONFIG_HOME}${ERROR}) not found."
        abort
    fi

    if [ -d "${ks_dir}" ]; then
        verify_version "KlipperScreen" "${ks_dir}" "${ks_oldest_id}" "${ks_latest_id}"
    fi
}

set_serial_id() {
    mapfile -t ids < <(ls /dev/serial/by-id/ | grep "vivid\|buffer" 2>/dev/null)
    local ids_num=${#ids[@]}
    log_echo "${TITLE}${SECTION}"
    if [ "${ids_num}" == 0 ]; then
        log_echo "${WARNING}Device serial id not found, please confirm if the ViViD cable is properly plugged in."
        yn=$(prompt_yn "Do not configure the serial id for now, manually modify it after installation is complete.")
        echo
        if [ "$yn" = "n" ]; then
            abort
        fi
    else
        if [ "${ids_num}" == 1 ]; then
            log_echo "${WARNING}Only 1 serial id was found. ViViD requires at least 2(ViViD + buffer). Please confirm if the ViViD cable is properly plugged in."
            yn=$(prompt_yn "Configure one first, and manually modify the rest after installation is complete.")
            echo
            if [ "$yn" = "n" ]; then
                abort
            fi
        fi

        for id in "${ids[@]}"; do
            if [[ "$id" == *"vivid"* ]]; then
                log_echo "${PROMPT}Is ${PURPLE}${id}${PROMPT} a ${PURPLE}ViViD${PROMPT} serial port?"
                yn=$(prompt_yn "ViViD MCU serial id")
                echo
                if [ "$yn" = "y" ]; then
                    g_vivid_id=${id}
                fi
            elif [[ "$id" == *"buffer"* ]]; then
                log_echo "${PROMPT}Is ${PURPLE}${id}${PROMPT} a ${PURPLE}Buffer${PROMPT} serial port?"
                yn=$(prompt_yn "Buffer MCU serial id")
                echo
                if [ "$yn" = "y" ]; then
                    g_buffer_id=${id}
                fi
            fi
        done
    fi
}

set_cutter() {
    # Cutter
    log_echo "${TITLE}${SECTION}"
    log_echo "${PROMPT}Installing the ${PURPLE}Cutter${PROMPT} is crucial!"
    yn=$(prompt_yn "Has the cutter been installed?")
    echo
    if [ "$yn" = "n" ]; then
        log_echo "${ERROR}Installing a cutter is crucial, printing multi-colors may damage the ViViD and the printer without a cutter."
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
    log_echo "${TITLE}${SECTION}"
    log_echo "${PROMPT}Installing an ${PURPLE}Entry Sensor${PROMPT} in toolhead can improve the accuracy of ViViD in identifying the location of filament!"
    yn=$(prompt_yn "Has the entry sensor been installed?")
    echo
    if [ "$yn" = "n" ]; then
        log_echo "${WARNING}Installing an entry sensor is highly recommended, as it can improve the accuracy of ViViD in identifying the location of filament."
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

set_purge_brush() {
    log_echo "${TITLE}${SECTION}"
    log_echo "${PROMPT}If ${PURPLE}purge${PROMPT} is enabled, the old filament can be quickly purged out."
    log_echo "${PROMPT}If ${PURPLE}brush${PROMPT} is enabled, it will clean up scrap stuck to the nozzle with a brush before start/resume printing."
    log_echo "${PROMPT}Do you want to enable ${PURPLE}purge${PROMPT} and ${PURPLE}brush"
    yn=$(prompt_yn "Enable")
    echo
    if [ "$yn" = "n" ]; then
        g_purge_brush=0
    else
        g_purge_brush=1
    fi
}

set_klipper_screen() {
    if [ ! -d "${ks_dir}" ]; then
        return
    fi

    log_echo "${TITLE}${SECTION}"
    log_echo "${PROMPT}Installing ${PURPLE}KlipperScreen for ViViD${PROMPT} will add a ViViD management menu to KlipperScreen."
    yn=$(prompt_yn "Install KlipperScreen?")
    echo
    if [ "$yn" = "y" ]; then
        g_klippe_screen=1
    else
        g_klippe_screen=0
    fi
}

get_version() {
    VIVID_VER=$(git -C "${SHELL_DIR}" describe --tags --always 2>/dev/null || printf "%s" "unversioned candidate")
    log_echo "${TITLE}${SECTION}"
    log_echo "${INFO}ViViD script: ${B_GREEN}${VIVID_VER}"
    log_echo "${INFO}Klipper: ${B_GREEN}${KLIPPY_VERSION}"
    log_echo "${INFO}KlipperScreen: ${B_GREEN}${KLIPPER_SCREEN_VERSION}"
}

install_klippy() {
    # install klipper
    log_echo "${TITLE}${SECTION}"
    log_echo "${INFO}Installing ViViD to Klipper: ${PURPLE}${KLIPPY_VERSION}"
    if [ -d "${extras_dst_dir}" ]; then
        # link extras/mms/*.py
        # cd to ViViD source code path
        cd "${extras_src_dir}" || abort "Could not read ${extras_src_dir}"
        # traverse directory
        find . -type d | while IFS= read -r tmp_dir; do
            # remove '.'
            tmp_dir=${tmp_dir#.}
            # get source & destination path
            src_dir="${extras_src_dir}${tmp_dir}"
            dst_dir="${extras_dst_dir}${tmp_dir}"
            # create the same extension directory in klipper path
            mkdir -p "${dst_dir}" || exit 1
            # ban the null value, such as: don't set "*.py" to the value of 'file'
            shopt -s nullglob
            for file in "${src_dir}"/*.py; do
                ln -sf "${file}" "${dst_dir}/$(basename "${file}")" || exit 1
            done
            shopt -u nullglob
        done || abort "Could not link the ViViD Klipper module"
        log_echo "${INFO}ViViD ${PURPLE}${extras_dst_dir}${INFO} link completed!"
        # patch neopixel
        if [[ -f "${neopixel}" ]]; then
            sed -i '/^BIT_MAX_TIME=.000004/ { s/^/# /; a\
BIT_MAX_TIME=.000030
            }' "${neopixel}"
            log_echo "${INFO}ViViD ${PURPLE}${neopixel}${INFO} patch completed!"
        else
            log_echo "${WARNING}ViViD ${PURPLE}${neopixel}${INFO} not patched!"
        fi

        # patch aht30
        if [ "${g_aht30_patch}" == 1 ]; then
            if [[ -f "${aht30}" ]]; then
                sed -i '/^[[:space:]]*'"'"'INIT'"'"'[[:space:]]*:[[:space:]]*\[0xE1, 0x08, 0x00\],/ { s/^/# /; a\
    '"'"'INIT'"'"'              :[0xBE, 0x08, 0x00],
                }' "${aht30}"
                log_echo "${INFO}ViViD ${PURPLE}${aht30}${INFO} patch completed!"
            else
                log_echo "${WARNING}ViViD ${PURPLE}${aht30}${INFO} not patched!"
            fi
        fi

        log_echo "${INFO}ViViD for klipper installation completed!"
    else
        abort "ViViD not installed because ${PURPLE}${extras_dst_dir}${ERROR} directory not found!"
    fi
}

uninstall_klippy() {
    # uninstall klipper
    log_echo "${TITLE}${SECTION}"
    log_echo "${INFO}Unlinking ViViD from Klipper..."
    if [ -d "${extras_dst_dir}" ]; then
        # unlink extras/mms/*.py
        rm -rf "${extras_dst_dir}/mms" || abort "Could not unlink the ViViD Klipper module"
        log_echo "${PURPLE}${extras_dst_dir}${INFO} removed!"
        # unpatch neopixel
        if [[ -f "${neopixel}" ]]; then
            sed -i 's/^[[:space:]]*#[[:space:]]*BIT_MAX_TIME=.000004/BIT_MAX_TIME=.000004/' "${neopixel}"
            sed -i '/^BIT_MAX_TIME=.000030/d' "${neopixel}"
            log_echo "${PURPLE}${neopixel}${INFO} unpatched!"
        fi
        # unpatch aht30
        if [ "${g_aht30_patch}" == 1 ]; then
            if [[ -f "${aht30}" ]]; then
                sed -i '/^#\s*'\''INIT'\''\s*:\[0xE1, 0x08, 0x00\],$/ s/^# //'  "${aht30}"
                sed -i '/^[[:space:]]*'\''INIT'\''[[:space:]]*:[[:space:]]*\[0xBE, 0x08, 0x00\],$/d' "${aht30}"
                log_echo "${PURPLE}${aht30}${INFO} unpatched!"
            fi
        fi
        log_echo "${INFO}ViViD for klipper uninstallation completed!"
    else
        log_echo "${WARNING}ViViD not uninstalled because ${PURPLE}${extras_dst_dir}${WARNING} directory not found!"
    fi
}

copy_config_files() {
    local mode=$1
    local dst_dir="${g_mms_dir}"
    local old_config="${g_old_config_path}"
    local src_dir="${SHELL_DIR}/config/bigtreetech-mms"
    local backup="" staging relative old_dir

    if [[ -z "${old_config}" && -f "${g_mms_path}" ]]; then
        old_config="${g_mms_path}"
    fi

    staging=$(mktemp -d "${KLIPPER_CONFIG_HOME}/.vivid-config.XXXXXX") ||
        abort "Could not prepare a temporary ViViD configuration"
    if ! (
        cp -r -- "${src_dir}/." "${staging}" || exit 1
        if [ -d "${dst_dir}" ]; then
            chmod --reference="${dst_dir}" "${staging}" || exit 1
        else
            chmod --reference="${src_dir}" "${staging}" || exit 1
        fi
        if [ -n "${old_config}" ]; then
            old_dir="${old_config%/*}"
            if [[ "${old_dir}" == */mms ]]; then
                old_dir="${old_dir%/mms}"
            fi
            for relative in mms-slot-meta.json extend/mms-extend.cfg extend/mms-extend-factory.cfg; do
                if [ -f "${old_dir}/${relative}" ]; then
                    cp -p -- "${old_dir}/${relative}" "${staging}/${relative}" || exit 1
                fi
            done
            if [[ -f "${old_dir}/mms/mms-includes.cfg" ]] &&
                grep -Eq '^[[:space:]]*\[include[[:space:]]+\.\./extend/mms-extend\.cfg\][[:space:]]*([#;].*)?$' \
                    "${old_dir}/mms/mms-includes.cfg"; then
                printf '\n[include ../extend/mms-extend.cfg]\n' >> "${staging}/mms/mms-includes.cfg" || exit 1
            fi
        fi
        g_mms_dir="${staging}"
        g_mms_path="${staging}/mms/mms.cfg"
        g_old_config_path="${old_config}"
        if [ "${mode}" = install ]; then
            set_user_config
        else
            sync_config_opt_enable "{}" && sync_config_val_copy "{}"
        fi
    ); then
        rm -rf -- "${staging}"
        abort "Could not migrate the ViViD configuration. The installed configuration was kept."
    fi

    if [[ -d "${dst_dir}" ]]; then
        backup=$(mktemp -d "${dst_dir}-$(date '+%Y%m%d_%H%M%S')-XXXXXX") || {
            rm -rf -- "${staging}"
            abort "Could not create a ViViD configuration backup"
        }
        if ! mv -T -- "${dst_dir}" "${backup}"; then
            rmdir -- "${backup}"
            rm -rf -- "${staging}"
            abort "Could not back up ${dst_dir}"
        fi
    fi
    if ! mv -T -- "${staging}" "${dst_dir}"; then
        if [[ -n "${backup}" ]] && ! mv -T -- "${backup}" "${dst_dir}"; then
            log_echo "${ERROR}Could not restore the original configuration from ${backup}"
        fi
        rm -rf -- "${staging}"
        abort "Could not install the new ViViD configuration"
    fi
    if [[ -n "${backup}" ]]; then
        log_echo "${INFO}Old config backup completed: ${PURPLE}${backup}"
    fi
}

edit_vivid_config() {
    local kind=$1 mode=$2 file
    shift 2
    case "${kind}" in
        klipper) file="${KLIPPER_CONFIG_HOME}/printer.cfg" ;;
        moonraker) file="${KLIPPER_CONFIG_HOME}/moonraker.conf" ;;
    esac
    python3 -B "${SHELL_DIR}/scripts/install_config.py" "${kind}" "${mode}" "${file}" "$@"
}

verify_linked_module() {
    local checkout=$1 relative=$2 suffix=$3 target="$1/$2"
    local tracked unexpected link
    tracked=$(git -C "${checkout}" ls-files -- "${relative}" 2>/dev/null) || tracked=""
    if [[ -n "${tracked}" ]]; then
        abort "The ViViD installer cannot replace or remove Git-managed files in ${target}"
    fi
    if [[ ! -e "${target}" && ! -L "${target}" ]]; then
        return
    fi

    if [[ -L "${target}" && "${relative}" == *.py ]]; then
        [[ "$(readlink -- "${target}")" == */"${suffix}" ]] ||
            abort "The existing link at ${target} is not a ViViD installer link"
        return
    fi
    if [[ ! -d "${target}" || -L "${target}" ]]; then
        abort "The ViViD installer cannot replace or remove unmanaged path ${target}"
    fi
    unexpected=$(find "${target}" ! -type d ! -type l \
        ! \( -type f -path '*/__pycache__/*.pyc' \) -print -quit) ||
        abort "Could not inspect ${target}"
    if [[ -n "${unexpected}" ]]; then
        abort "The ViViD installer cannot remove unmanaged file ${unexpected}"
    fi
    while IFS= read -r -d '' link; do
        if [[ "$(readlink -- "${link}")" != */"${suffix}/${link#"${target}/"}" ]]; then
            abort "The existing link at ${link} is not a ViViD installer link"
        fi
    done < <(find "${target}" -type l -print0)
}

verify_install_layout() {
    local mode=$1
    verify_linked_module "${KLIPPER_HOME}" "${EXTRAS_DIR}/mms" "${EXTRAS_DIR}/mms"
    verify_linked_module "${ks_dir}" vivid "${VIVID_DIR}"
    verify_linked_module "${MOONRAKER_COMPONENTS_DIR}" mms_server.py moonraker/components/mms_server.py

    g_old_config_path=$(edit_vivid_config klipper "${mode}" --check) ||
        abort "Could not prepare the Klipper configuration"
    if [[ "${mode}" == remove || -d "${MOONRAKER_COMPONENTS_DIR}" ]]; then
        edit_vivid_config moonraker "${mode}" --check ||
            abort "Could not prepare the Moonraker configuration"
    fi
}

install_moonraker() {
    log_echo "${TITLE}${SECTION}"
    log_echo "${INFO}Installing ViViD Moonraker component..."
    if [ -d "${MOONRAKER_COMPONENTS_DIR}" ]; then
        local src_file="${SHELL_DIR}/moonraker/components/mms_server.py"
        local dst_file="${MOONRAKER_COMPONENTS_DIR}/mms_server.py"
        ln -sf "${src_file}" "${dst_file}" || abort "Could not link ${dst_file}"
        if [ -f "${KLIPPER_CONFIG_HOME}/moonraker.conf" ]; then
            edit_vivid_config moonraker install ||
                abort "Could not configure the ViViD Moonraker component"
        else
            log_echo "${WARNING}moonraker.conf not found. Add [mms_server] to your Moonraker configuration."
        fi
        log_echo "${INFO}ViViD Moonraker component linked to ${PURPLE}${dst_file}${INFO}"
    else
        log_echo "${WARNING}Moonraker components directory not found at ${MOONRAKER_COMPONENTS_DIR}. Skipping component installation."
    fi
}

uninstall_moonraker() {
    log_echo "${TITLE}${SECTION}"
    log_echo "${INFO}Unlinking ViViD Moonraker component..."
    edit_vivid_config moonraker remove ||
        abort "Could not remove the ViViD Moonraker configuration"
    local dst_file="${MOONRAKER_COMPONENTS_DIR}/mms_server.py"
    if [ -L "${dst_file}" ]; then
        rm -f "${dst_file}" || abort "Could not unlink ${dst_file}"
        log_echo "${INFO}Removed ${dst_file}"
    fi
}

verify_KlipperScreen_startup() {
    if [ ! -f "${ks_src_dir}/installer.py" ]; then
        abort "Cannot find the ViViD KlipperScreen module in ${ks_src_dir}"
    fi
    if [[ ! -f "${screen}" || $(grep -Ec "${ks_activate_pattern}" "${screen}") != 1 ]]; then
        abort "Cannot locate the KlipperScreen startup hook in ${screen}"
    fi
}

unpatch_KlipperScreen() {
    local start_line end_line

    # Remove the startup hook and the legacy direct subscription patch.
    if [[ -f "${screen}" ]]; then
        sed -i -e '/^[[:space:]]*from[[:space:]]\+vivid\.installer[[:space:]]\+import[[:space:]]\+install_vivid[[:space:]]*$/d' \
                -e '/^[[:space:]]*install_vivid(self\.base_panel)[[:space:]]*$/d' \
                -e '/^[[:space:]]*requested_updates\['\''objects'\''\]\["mms"\][[:space:]]*=[[:space:]]*\["slots", "steppers", "buffers"\][[:space:]]*$/d' \
                "${screen}" || return 1
    fi
    # Older installers added the ViViD print button directly to gcodes.py.
    if [[ -f "${gcodes}" ]]; then
        sed -i "\|^[[:space:]]*{\"name\": \"ViViD\", \"response\": 444, \"style\": 'dialog-info'},$|d" "${gcodes}" || return 1
        start_line=$(grep -nE -m1 '^[[:space:]]*elif response_id == 444:[[:space:]]*$' "${gcodes}" | cut -d: -f1)
        end_line=$(grep -n -F -m1 'parent_hook = self.confirm_print,' "${gcodes}" | cut -d: -f1)
        if [[ -n "${start_line}" && -n "${end_line}" && $start_line -lt $end_line ]]; then
            sed -i "${start_line},$((end_line + 1))d" "${gcodes}" || return 1
        fi
    fi
}

install_KlipperScreen() {
    # install KlipperScreen
    log_echo "${TITLE}${SECTION}"
    log_echo "${INFO}Installing ViViD to KlipperScreen ${PURPLE}${KLIPPER_SCREEN_VERSION}"
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

        log_echo "${INFO}ViViD ${PURPLE}${ks_dst_dir}${INFO} link completed!"

        # Remove any old hooks before inserting the startup entry.
        unpatch_KlipperScreen || abort "Could not remove the old KlipperScreen hooks"

        # ViViD registers the subscription and print dialog hooks at startup.
        sed -i -E "/${ks_activate_pattern}/i\\
        from vivid.installer import install_vivid\\
        install_vivid(self.base_panel)" "${screen}" ||
            abort "Could not patch the KlipperScreen startup hook in ${screen}"
        log_echo "${INFO}ViViD KlipperScreen ${PURPLE}${screen}${INFO} patch completed!"
    else
        abort "ViViD KlipperScreen not installed because ${PURPLE}${ks_dir}${ERROR} directory not found!"
    fi
}

uninstall_KlipperScreen() {
    # uninstall klipper
    log_echo "${TITLE}${SECTION}"
    log_echo "${INFO}Unlinking ViViD from KlipperScreen..."
    if [ -d "${ks_dir}" ]; then
        unpatch_KlipperScreen || abort "Could not remove the KlipperScreen hooks"
        log_echo "${PURPLE}${screen}${INFO} & ${PURPLE}${gcodes}${INFO} for ViViD unpatched!"

        rm -rf "${ks_dst_dir}" || abort "Could not remove ${ks_dst_dir}"
        log_echo "${PURPLE}${ks_dst_dir}${INFO} removed!"

        log_echo "${INFO}ViViD for KlipperScreen uninstallation completed!"
    else
        log_echo "${WARNING}ViViD KlipperScreen not uninstalled because ${PURPLE}${ks_dir}${WARNING} directory not found!"
    fi
}

sync_config_opt_enable() {
    config_opt=$1
    python3 -B "${SHELL_DIR}/scripts/config_opt_enable.py" "${g_mms_path}" "${g_old_config_path}" "${config_opt}"
}

sync_config_val_copy() {
    config_val=$1
    python3 -B "${SHELL_DIR}/scripts/config_val_copy.py" "${g_mms_path}" "${g_old_config_path}" "${config_val}"
}

set_user_config() {
    local base_dir="${KLIPPER_CONFIG_HOME}/bigtreetech-mms/base"
    local cut_path="${base_dir}/mms-cut.cfg"
    local purge_brush_path="${base_dir}/mms-purge.cfg"

    # aht30 config [ATH10/AHT3X]
    if [ "${g_aht30_patch}" == 1 ]; then
        local aht30_cfg="${g_mms_dir}/hardware/mms-heater.cfg"
        sed -i 's/sensor_type:          AHT3X/sensor_type:          AHT10/g' "${aht30_cfg}" || return 1
    fi

    log_echo "${PURPLE}Cutter${INFO} must be configured, please configure the specific position in ${PURPLE}${cut_path}"

    # entry sensor
    export entry_sensor=${g_entry_sensor}
    if [ "${g_entry_sensor}" -eq 1 ]; then
        log_echo "${PURPLE}Entry Sensor${INFO} has been enabled, please configure the specific pin in ${PURPLE}${KLIPPER_CONFIG_HOME}/bigtreetech-mms/mms/mms.cfg"
    fi

    # purge_brush
    export purge=${g_purge_brush}
    export brush=${g_purge_brush}
    if [ "${g_purge_brush}" -eq 1 ]; then
        log_echo "${PURPLE}purge${INFO} and ${PURPLE}brush${INFO} has been enabled, please configure the specific position in ${PURPLE}${purge_brush_path}"
    fi

    # vivid seral id
    if [ -z "${g_vivid_id}" ]; then
        log_echo "${WARNING}ViViD MCU serial id has not been set. Please modify it manually in ${PURPLE}${KLIPPER_CONFIG_HOME}/bigtreetech-mms/mms/mms.cfg"
    else
        log_echo "${INFO}ViViD Serial ID: ${PURPLE}${g_vivid_id}"
        export vivid_id="/dev/serial/by-id/${g_vivid_id}"
    fi
    # buffer seral id
    if [ -z "${g_buffer_id}" ]; then
        log_echo "${WARNING}Buffer MCU serial id has not been set. Please modify it manually in ${PURPLE}${KLIPPER_CONFIG_HOME}/bigtreetech-mms/mms/mms.cfg"
    else
        log_echo "${INFO}Buffer Serial ID: ${PURPLE}${g_buffer_id}"
        export buffer_id="/dev/serial/by-id/${g_buffer_id}"
    fi

    config_opt=`python3 -c '
import json
import os
data = {
    "mms": {
        "entry_sensor": os.environ.get("entry_sensor", None),
    }
}
print(json.dumps(data, ensure_ascii=False))
'`
    sync_config_opt_enable "${config_opt}" || return 1

config_val=`python3 -c '
import json
import os
data = {
    "mcu buffer": {
        "serial": os.environ.get("buffer_id", None),
    },
    "mcu vivid": {
        "serial": os.environ.get("vivid_id", None),
    },
    "mms purge": {
        "enable": os.environ.get("purge", None),
    },
    "mms brush": {
        "enable": os.environ.get("brush", None),
    }
}
print(json.dumps(data, ensure_ascii=False))
'`
    sync_config_val_copy "${config_val}"
}

prompt_service_restart() {
    service=$1
    action=$2
    log_echo "${PURPLE}${service}${INFO} has been ${PURPLE}${action}${INFO}. Restart immediately? (This will interrupt printing if there are any ongoing tasks.)"
    yn=$(prompt_yn "Restart")
    echo
    if [ "$yn" = "y" ]; then
        sudo systemctl restart ${service}.service
        log_echo "${INFO}The ${PURPLE}${service}${INFO} service has been restarted."
    else
        log_echo "${WARNING}The ${PURPLE}${service}${WARNING} service needs to be restarted for it to take effect. Please manually restart it later."
    fi
}

install_vivid() {
    set_serial_id
    set_cutter
    set_entry_sensor
    set_purge_brush
    set_klipper_screen
    if [ "${g_klippe_screen}" -eq 1 ]; then
        verify_KlipperScreen_startup
    fi

    verify_install_layout install
    copy_config_files install

    # uninstall for cleanup
    log_disable
    uninstall_klippy
    uninstall_KlipperScreen
    log_enable

    install_klippy
    install_moonraker

    edit_vivid_config klipper install || abort "Could not install the ViViD configuration"
    if [ "${g_klippe_screen}" -eq 1 ]; then
        install_KlipperScreen
    fi

    prompt_service_restart klipper installed
    prompt_service_restart moonraker installed
    if [ "${g_klippe_screen}" -eq 1 ]; then
        prompt_service_restart KlipperScreen installed
    fi

    log_echo "${TITLE}${SECTION}"
    log_echo "${INFO}ViViD MCU serial id: ${GREEN}${g_vivid_id}"
    log_echo "${INFO}Buffer MCU serial id: ${GREEN}${g_buffer_id}"
    log_echo "${INFO}Cutter: ${GREEN}${g_cutter}"
    log_echo "${INFO}Entry Sensor: ${GREEN}${g_entry_sensor}"
    log_echo "${INFO}Purge & Brush: ${GREEN}${g_purge_brush}"
    log_echo "${INFO}KlipperScreen: ${GREEN}${g_klippe_screen}"

    get_version

    log_echo "${TITLE}${SECTION}"
    log_echo "${GREEN}ViViD installation is complete."
}

update_vivid() {
    if [ -d "${ks_dst_dir}" ]; then
        g_klippe_screen=1
        verify_KlipperScreen_startup
    else
        g_klippe_screen=0
    fi
    verify_install_layout update
    copy_config_files update

    # uninstall for cleanup
    log_disable
    uninstall_klippy
    uninstall_KlipperScreen
    log_enable

    # reinstall klipper
    install_klippy
    install_moonraker
    edit_vivid_config klipper update || abort "Could not update the ViViD configuration"
    # reinstall KlipperScreen
    if [ "${g_klippe_screen}" -eq 1 ]; then
        install_KlipperScreen
    fi

    prompt_service_restart klipper updated
    prompt_service_restart moonraker updated
    if [ "${g_klippe_screen}" -eq 1 ]; then
        prompt_service_restart KlipperScreen updated
    fi

    get_version

    log_echo "${TITLE}${SECTION}"
    log_echo "${GREEN}ViViD update is complete."
}

uninstall_vivid() {
    verify_install_layout remove
    edit_vivid_config klipper remove || abort "Could not remove the ViViD Klipper configuration"
    uninstall_klippy
    uninstall_moonraker
    prompt_service_restart klipper uninstalled
    prompt_service_restart moonraker uninstalled

    if [ -d "${ks_dir}" ]; then
        uninstall_KlipperScreen
        prompt_service_restart KlipperScreen uninstalled
    fi

    log_echo "${GREEN}ViViD uninstallation is complete."
}

usage() {
    log_echo "${EMPHASIZE}"
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
while getopts "iduzgh" arg; do
    case $arg in
        i) INSTALL=1;;
        d) UNINSTALL=1;;
        u) UPDATE=1;;
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
    log_echo "${ERROR}Can't install and uninstall at the same time!"
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
elif [ "${UPDATE}" == 1 ]; then
    update_vivid
else
    install_vivid # default install
fi

# step 5: end
log_echo ""
