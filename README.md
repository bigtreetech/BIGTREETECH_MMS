### Compatibility
* Klipper: compatible between [Commits on Nov 27, 2025: stm32: f0 i2c clean nackcf interrupt on handle](https://github.com/Klipper3d/klipper/commit/938300f3c3cc25448c499a3a8ca5b47b7a6d4fa8) and the lastest version [Commits on Dec 1, 2025: ads1x1x: Interface for "QUERY_ADC" (#7132)](https://github.com/Klipper3d/klipper/commit/9c84895a09fa408b2838ce85a2540ee7d4eeb117). The stepper scheduling uses the newly updated `motion_queuing.py` upstream of Klipper, so it is necessary to use the new version of Klipper.
* KlipperScreen: compatible between [Commits on Sep 12, 2025: refactor: less logging when on battery](https://github.com/KlipperScreen/KlipperScreen/commit/b3115f9b9b329642d4dbf0ad225ab065ea3eda80) and the lastest version [Commits on Nov 29, 2025: fix: handle invalid UTF-8 characters in WiFi SSID (#1621)](https://github.com/KlipperScreen/KlipperScreen/commit/61f7afd1e21f7b022e7a6bfb29992d3c396a5c50). In theory, earlier KlipperScreen also supports it, but it has not been actually tested yet.
* Python: Only supports Klipper for Python 3 environment.

### Installation
* Download installation script.

    ```
    cd ~
    git clone https://github.com/bigtreetech/BIGTREETECH_ViViD.git
    cd ~/BIGTREETECH_ViViD
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

### 🐇 Acknowledgements
The script implementation referenced the logic and some source code from the excellent project [Happy-Hare](https://github.com/moggieuk/Happy-Hare).

Thanks to the open source community for creating such valuable resources, so we are able to build upon.
