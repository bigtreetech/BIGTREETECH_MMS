### Compatibility
* Klipper: compatible between [Commits on Nov 27, 2025: stm32: f0 i2c clean nackcf interrupt on handle](https://github.com/Klipper3d/klipper/commit/938300f3c3cc25448c499a3a8ca5b47b7a6d4fa8) and the lastest version [Commits on Dec 31, 2025: avr: add lgt8f328p support](https://github.com/Klipper3d/klipper/commit/dd625933f7b9bd53363fe015c62aaa874021fa9a). The stepper scheduling uses the newly updated `motion_queuing.py` upstream of Klipper, so it is necessary to use the new version of Klipper.
* KlipperScreen: compatible between [Commits on Sep 12, 2025: refactor: less logging when on battery](https://github.com/KlipperScreen/KlipperScreen/commit/b3115f9b9b329642d4dbf0ad225ab065ea3eda80) and the lastest version [Commits on Dec 27, 2025: Fix: vertical mode not updating properly on screen size change (#1631)](https://github.com/KlipperScreen/KlipperScreen/commit/0747a7a150a592be2b555d86b1f1aef6632cfec9). In theory, earlier KlipperScreen also supports it, but it has not been actually tested yet.
* Python: Only supports Klipper for Python 3 environment.

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
    [-h] [-i] [-d] [-z] [-g]
    ```

    * `-h`: help

    * `-i`: install

    * `-d`: uninstall

    * `-z`: skip github update check. The script will automatically check the version on GitHub by default and ensure that it runs with the latest version. If you have modified some logic of script locally, please disable updates at runtime through the `-z` parameter. For example:

        * Do not update installation: `./install.sh -z` or`./install.sh -zi`

        * Do not update uninstallation: `./install.sh -zd`

    * `-g`: get version

    * no flags for default `-i` install

### Flash
Both ViViD and Buffer MCU have built-in [Katapult (formerly known as CanBoot)](https://github.com/Arksine/katapult) for updating Klipper firmware.

We recommend using the `flash.sh` script provided here to update the firmware of ViViD and Buffer, rather than directly using the Katapult command, as there will be an additional step to verify the binary content of the firmware, try to avoid startup issues caused by flashing incorrect firmware as much as possible.

`flash.sh` will list devices with serial id containing `vivid` or `buffer`, select the device ID we want to flash to start flashing. If no parameter are included, the [factory firmware](./firmware/) will be flashed by default. We can also specify the binary file to be flashed through the `-f` parameter.

for example:

* `flash.sh`
* `flash.sh -f ~/klipper/out/klipper.bin`

### Config
Please refor to [mms_config](./docs/en/mms_config.md) for details.

### ChangeLog
Please refor to [mms_changelog](./docs/en/mms_changelog.md) for details.

### 🐇 Acknowledgements
The script implementation referenced the logic and some source code from the excellent project [Happy-Hare](https://github.com/moggieuk/Happy-Hare).

Thanks to the open source community for creating such valuable resources, so we are able to build upon.
