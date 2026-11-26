### Compatibility
* Klipper MMS 0.1.0496 requires Klipper [938300f](https://github.com/Klipper3d/klipper/commit/938300f3c3cc25448c499a3a8ca5b47b7a6d4fa8) or newer and a printer configuration with `[pause_resume]`. Offline verification covers that revision and the source tree based on [7558e8c2](https://github.com/Klipper3d/klipper/commit/7558e8c2f6f3f2ca4b5ff1ddd85d2b82f0c3881d).
* KlipperScreen ViViD 0.2.0042 was checked against [b3115f9b](https://github.com/KlipperScreen/KlipperScreen/commit/b3115f9b9b329642d4dbf0ad225ab065ea3eda80) and [22604c77](https://github.com/KlipperScreen/KlipperScreen/commit/22604c770ce199edd057c095680ec8751184765e).
* G-code upload processing requires Moonraker with the [file processor API](https://github.com/Arksine/moonraker/commit/b8b28bc). With an older Moonraker, ordinary tool changes and already processed files still work.
* The installer requires Python 3.

### Installation
* Download installation script.

    ```
    cd ~
    git clone https://github.com/bigtreetech/BIGTREETECH_MMS.git
    cd ~/BIGTREETECH_MMS
    ```
* Start Installation

    ```
    ./install.sh
    ```

    Running supports the following parameters: 

    ```
    [-h] [-i] [-d] [-u] [-z] [-g]
    ```

    * `-h`: help

    * `-i`: install

    * `-d`: uninstall

    * `-u`: Update ViViD for Klipper, Moonraker, and any previously installed ViViD KlipperScreen. The installer backs up the old Klipper configuration, migrates supported machine settings and slot calibration, and installs the current bundled macros. Existing Moonraker settings are preserved.

    * `-z`: skip github update check. The script will automatically check the version on GitHub by default and ensure that it runs with the latest version. If you have modified some logic of script locally, please disable updates at runtime through the `-z` parameter. For example:

        * Do not update installation: `./install.sh -z` or`./install.sh -zi`

        * Do not update uninstallation: `./install.sh -zd`

    * `-g`: get version

    * no flags for default `-i` install

The installer manages linked copies of the ViViD modules. For a Klipper or KlipperScreen source checkout that already includes ViViD, use that checkout's update process.

The installer follows configuration includes. Use a dedicated Klipper include for the ViViD entry point so the installer can remove it during uninstall without removing other configuration files.

### Flash
Both ViViD and Buffer MCU have built-in [Katapult (formerly known as CanBoot)](https://github.com/Arksine/katapult) for updating Klipper firmware.

We recommend using the `flash.sh` script provided here to update the firmware of ViViD and Buffer, rather than directly using the Katapult command, as there will be an additional step to verify the binary content of the firmware, try to avoid startup issues caused by flashing incorrect firmware as much as possible.

`flash.sh` will list devices with serial id containing `vivid` or `buffer`, select the device ID we want to flash to start flashing. If no parameter are included, the [factory firmware](./firmware/) will be flashed by default. We can also specify the binary file to be flashed through the `-f` parameter.

for example:

* `flash.sh`
* `flash.sh -f ~/klipper/out/klipper.bin`

### Config
See the MMS configuration guides in [English](./docs/en/mms_config.md) and [Chinese](./docs/zh/mms_config.md). The [pause recovery and cutter protection examples](./docs/en/mms_config.md#pause-recovery-and-cutter-protection) show how to integrate these features with the printer’s existing macros.

### ChangeLog
See the release notes in [English](./docs/en/Changelog.md) and [Chinese](./docs/zh/Changelog.md).

### 🐇 Acknowledgements
The script implementation referenced the logic and some source code from the excellent project [Happy-Hare](https://github.com/moggieuk/Happy-Hare) and [AFC](https://github.com/ArmoredTurtle/AFC-Klipper-Add-On).

Thanks to the open source community for creating such valuable resources, so we are able to build upon.
