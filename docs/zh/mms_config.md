### mms/mms.cfg

#### MCU Configuration

<img src="../img/mms_mcu.png" width="800"/>

我们需要把设备实际的 serial id 填写到 `①: Buffer` 和 ` ②: ViViD` 位置，serial id 可使用以下两种方式获取

* 在 ssh 终端，通过命令查询, id 中有 `stm32f042x6_buffer`的是 `Buffer`, 有 `stm32g0b1xx_vivid`的是 ` ViViD`。

    ```
    ls /dev/serial/by-id/*
    ```

    <img src="../img/serial_id.png" width="800"/>

* 在 mainsail 中

    <img src="../img/devices.png" width="1000"/>

    * `① DEVICES`
    * `② SERIAL`: 查询 serial id 界面
    * `③ REFRESH`: 扫描 serial id
    * `④ Path by ID`: id 中带有 `stm32f042x6_buffer`的是 `Buffer`, 有 `stm32g0b1xx_vivid`的是 `ViViD`
    * `⑤`: 复制 id, 然后将复制的 id 粘贴到对应的配置中即可

#### MCU Temperature

<img src="../img/mcu_temper.png" width="500"/>

`①` 和 `②` 分别为 Buffer 和 ViViD 的 MCU 温度, 启用此配置后在 Mainsail (如下图所示) 和 KlipeprScreen 上都会显示对应的温度, 并且 klipper 也会将实时温度记录到 log 中, 可用于排查故障。所以请不要修改此配置, 除非你明确的知道它意味着什么。

<img src="../img/mcu_temper_val.png" width="500"/>

#### Module Includes

<img src="../img/mms_includes.png" width="500"/>

此配置包含并启动了 ViViD 所有子模块的功能, 请不要修改`此处`以及`mms-includes.cfg` 中的内容。

#### MMS Main Settings

<img src="../img/mms_mms.png" width="800"/>

* ①: ViViD 任务失败后重试的次数。例如 `将耗材从Inlet加载到Gate中` 任务, 进料的长度超过设置的最大长度后Gate传感器仍然没有触发, 会认为此次任务执行失败并重试。重试超过 `retry_times` 次后如果仍然未恢复正常, 则会中止任务(如果打印机`正在打印`，ViViD也会发出`暂停打印`的指令)并抛出异常信息用于诊断, 排除故障后可继续任务。
* ②: ViViD 实际硬件映射关系的配置, 请不要修改这里。
* ③: Entry Sensor 一般安装在挤出机齿轮稍微上面的位置, 用于检测耗材是否到达了挤出机上方。

    强烈建议打印机安装此传感器并启用此配置。

    可以删除配置项最前面的 `#` 和 空格来启用此配置, 同时也需要将pin(图中的`EBBCan:gpio21`位置) 修改为传感器实际连接的pin。
* ④: 断料检测。实时监听 inlet 传感器的状态, 如果检测到当前所用的料槽没有耗材, 则会立即暂停打印。
* ⑤: 自动续料。当前`打印中`所用的料槽检测到断料后, 自动使用`mms-slot.cfg`文件中配置的`substitute_with`料槽续料。(`fracture_detection_enable`断料检测必须启用才可使用此功能)

#### MMS Logger Configuration

<img src="../img/mms_logger.png" width="700"/>

log 日志相关配置, 默认配置遵循 klipper 规范, 请不要修改此配置, 除非你明确的知道它意味着什么。

#### Macros Includes

<img src="../img/mms_macros.png" width="500"/>

此配置包含了 ViViD 的宏命令配置。

### base/mms-cut.cfg

#### [mms cut]

<img src="../img/cut.png" width="800"/>

移动 toolhead 撞击固定位置来触发切刀切料。

* enable: 启用或禁用此模块
* z_raise: 执行 MMS_CUT 动作前, z 轴抬升的高度, 命令执行完成后 z 轴高度会恢复原始高度。

    此参数仅作用于手动执行 MMS_CUT 命令。
    
    换料流程中的 cut 动作不会额外应用此参数抬升 z 轴，而是由`[mms swap]`中的参数统一抬升 z 轴
* cutter_init_point: 预备切料时 toolhead 需要处于的位置, 请修改为打印机实际的坐标位置。
* cutter_final_point: 耗材已被切断时 toolhead 需要处于的位置, 请修改为打印机实际的坐标位置。
* cut_speed: toolhead 由 `cutter_init_point` 到 `cutter_final_point` 之间的移动速度。


### base/mms-motion.cfg

#### [mms delivery]

<img src="../img/motion_delivery.png" width="800"/>

* speed_selector: 选料电机运动的速度, `mms-stepper.cfg` 中的传动距离为 `360/2.5`=144mm/转, 所以默认的 150mm/s ≈ 1.04 转/秒
* accel_selector: 选料电机运动的加速度
* speed_drive: 送料电机运动的速度, `mms-stepper.cfg` 中的传动距离为 `360/43`≈ 8.37mm/转, 所以默认的 80mm/s ≈ 9.56 转/秒
* accel_drive: 送料电机运动的加速度
* stepper_move_distance: 耗材在 `Inlet` 到 `Buffer`, 或者 `Buffer` 到 `Extruder` 之间进退料时, 单次移动的最大长度。如果超出此长度后对应的传感器仍然没有触发, 则判定此次进退料异常。
* safety_retract_distance: 耗材由 `Extruder` 退到 `Buffer` 时, `Gate` 传感器释放后, 再多退出 `safety_retract_distance` 长度的耗材, 使耗材远离 `Gate` 传感器, 从而避免 `Gate` 传感器处于`触发`/`释放`的临界状态导致的误报。
* slots_loop_times: 执行 `MMS_SLOTS_LOOP` 自检命令时, 所有料槽自检的次数。4个料槽各进退料1次代表自检1次。

#### [mms autoload]

<img src="../img/motion_autoload.png" width="800"/>

耗材插入 `Inlet` 触发传感器后, 自动将此料槽耗材加载到 `Buffer` 中。

enable 默认关闭, 且仅在 ViViD 空闲时会自动进料。当 ViViD 正在进/退其他料槽的耗材或者正在打印时自动进料功能不会生效。

#### [mms charge]

<img src="../img/motion_charge.png" width="800"/>

ViViD 将耗材加载到 Extruder, Buffer 的 Outlet 传感器被触发, 此时耗材理应已处于 Extruder 的正上方, Extruder 挤出一小段距离(`extrude_distance`)尝试咬住耗材。

如果 Outlet 传感器被释放则证明 Extruder 已顺利咬住耗材, charge 完成。
如果 Outlet 未释放则重新挤出 `extrude_distance`, 最多尝试 `extrude_times` 次, 如果仍未释放则判定此次 charge 失败。

本模块用于保证耗材成功进入 Extruder。

* z_raise: 执行 MMS_CHARGE 动作前, z 轴抬升的高度, 命令执行完成后 z 轴高度会恢复原始高度。

    此参数仅作用于手动执行 MMS_CHARGE 命令。
    
    换料流程中的 charge 动作不会额外应用此参数抬升 z 轴，而是由`[mms swap]`中的参数统一抬升 z 轴

* extrude_distance: 挤出机挤出一小段距离尝试咬住耗材并释放 Outlet 传感器
* extrude_times: 一次 charge 最多挤出的次数
* extrude_speed: 挤出的速度
* distance_unload: charge 失败后将耗材抽出一部分来释放 Outlet
* custom_before: charge 之前执行的 gcode 命令, 用于自定义动作
* custom_after: charge 完成后执行的 gcode 命令, 用于自定义动作

#### [mms eject]

<img src="../img/motion_eject.png" width="800"/>

本模块用于保证耗材成功退出 Extruder。

* z_raise: 执行 MMS_EJECT 动作前, z 轴抬升的高度, 命令执行完成后 z 轴高度会恢复原始高度。

    此参数仅作用于手动执行 MMS_EJECT 命令。
    
    换料流程中的 eject 动作不会额外应用此参数抬升 z 轴，而是由`[mms swap]`中的参数统一抬升 z 轴
* Extruder

    Extruder回抽一段距离, 用于将已切断后的耗材, 从 Extruder 的齿轮中释放出来。回抽的总距离会被分解为 `retract_times` 个 `retract_distance`，也就是总共最多回抽 `retract_times * retract_distance` 长度的耗材。如果 `ViViD` 退料的逻辑已完成, Extruder 的回抽任务也会提前停止。

    * retract_distance: Extruder 回抽一小段距离
    * retract_times: Extruder 最多回抽的次数
    * retract_speed: Extruder 回抽的速度, 单位为 mm/min
* ViViD

    耗材切断后仍然有一小段耗材在 Extruder 的齿轮中夹着, 想要将当前耗材退回到 Buffer 中需要 ViViD 与 Extruder 一起同时退料, Extruder 退料将耗材从齿轮中释放, ViViD 将释放的耗材抽离 Extruder。

    原则上需要保证 `drive_speed` 小于 `retract_speed`。(注意 drive_speed 单位为 mm/s, retract_speed 单位为 mm/min, 需要额外换算一下)

    * drive_speed: ViViD 退料的速度, 单位为 mm/s
    * drive_accel: ViViD 退料的加速度。
    * distance_unload: ViViD 将耗材抽离 Extruder 的长度。

* custom_before: eject 之前执行的 gcode 命令, 用于自定义动作
* custom_after: eject 完成后执行的 gcode 命令, 用于自定义动作

#### [mms swap]

<img src="../img/motion_swap.png" width="800"/>

* enable: 此配置不会禁用 `custom_before` 和 `custom_after` 命令, 所以我们可以通过此配置禁用默认的 swap 换料流程, 使用脚本实施自定义的`换料`流程。
* z_raise: 换料前, z 轴抬升的高度, 换料完成后 z 轴高度会恢复原始高度。
* command_string: 换料 gcode 命令的名称, 默认的 `T` 意味着 gcode 命令为 `T0`,`T1`,`T2`,`T3`... 。请不要修改此配置, 除非你明确的知道它意味着什么。
* safe_mode: 每个 G1 移动命令后都添加 M400 逻辑, 用于等待当前移动命令执行完成后再进行一下步动作。请不要修改此配置, 除非你明确的知道它意味着什么。
* toolhead_move_speed: toolhead 的移动速度
* custom_before: swap 之前执行的 gcode 命令, 用于自定义动作
* custom_after: swap 完成后执行的 gcode 命令, 用于自定义动作


### base/mms-purge.cfg

#### [mms purge]

新的耗材加载到 Extruder 后, Extruder切刀下方以及Nozzle的腔体内还残留有旧的耗材, 我们需要此流程将旧耗材冲刷出Nozzle。

可以适当多挤出一些避免残留的旧耗材与新耗材混色。

<img src="../img/purge_purge_1.png" width="800"/>

* enable: 仅会禁用 `orphan_filament_length` 和 `purge_modifier` 设置的冲刷距离。
* z_raise: 执行 PURGE, MMS_PURGE, MMS_TRAY 或 MMS_TRAY_EJECT 动作前, z 轴抬升的高度, 命令执行完成后 z 轴高度会恢复原始高度。

    此参数仅作用于手动执行 MMS_EJECT 命令。
    
    换料流程中的 purge 动作不会额外应用此参数抬升 z 轴，而是由`[mms swap]`中的参数统一抬升 z 轴
* fan

    必需要配置 [[fan]](https://www.klipper3d.org/Config_Reference.html#fan)

    * fan_cooldown_speed: 冲刷完旧耗材后开启风扇的转速, 用于冷却喷嘴上残留的耗材, 便于后续使用brush刷子清理喷嘴。
    * fan_cooldown_wait: 风扇开启后等待`fan_cooldown_wait`秒来冷却耗材。

* purge
    * purge_speed: Exturder 冲刷旧耗材的挤出速度。
    * orphan_filament_length: 旧耗材剩余的长度。
    * purge_modifier: 旧耗材的冲刷倍率。

        Exturder实际冲刷的长度为 `orphan_filament_length * purge_modifier` 也就是默认冲刷 `60 *2.5 = 150mm`。
        
        `purge_modifier` 的设计初衷是: 虽然从切刀到Nozzle中剩余的旧耗材长度一样, 但是深颜色(dark)理应比浅颜色(bright)所需要的冲刷量更多一些, 因为深颜色更容易混色。所以理论上我们只需要设置统一的 `orphan_filament_length` , 然后通过旧耗材的颜色动态计算合适的 `purge_modifier` 冲刷倍率, 就可以在保证不混色的前提下尽可能的减少耗材冲刷从而减少浪费。但是现在这里还是固定值, 我们现在不需要修改此处。只需理解它的含义并设置合适的 `orphan_filament_length` 即可。

<img src="../img/purge_purge_2.png" width="800"/>

* Retraction
    * retraction_compensation: 冲刷完旧耗材后快速回抽一小段距离, 尽可能减少已融化的耗材从Nozzle流出。此参数需要与切片软件中的`换料前回抽`参数一致或者略多一些。例如 OrcaSlicer 中的配置在如下图中的位置 `Printer settings-> Extruder -> Retraction when switching material -> length`。

        <img src="../img/printer_settings.png" width="600"/>

    * retract_speed: 快速回抽的速度
* Nozzle Priming
    * nozzle_priming_dist: 使用 enable 禁用冲刷旧耗材后会自动启用此参数直接挤出固定长度的耗材。

        禁用冲刷旧耗材后，新耗材仍然需要多挤出一段距离。用于补充进料完成后, 新耗材与旧耗材之间仍然存在的间隔。
    * nozzle_priming_speed: 挤出耗材的速度
* Pressure Pulse Cleaning

    eject 退料前 Extruder 循环"挤出/回抽"耗材, 用于清理 Nozzle 上方的喉管, 避免零散的耗材残留在喉管上被碳化。也可以明显减少 Nozzle 自然流出掉落在平台上的垃圾耗材数量。

    * pulse_clean_enable: 启用/禁用此功能
    * pulse_rest_time: 每轮"挤出/回抽"中间暂停等待的秒数
    * pulse_count: "挤出/回抽"的次数
    * pulse_speed: "挤出/回抽"的速度
    * retract_dist: "回抽"的长度, 原则上 `retract_dist` 需要大于 `extrude_dist`
    * extrude_dist: "挤出"的长度

<img src="../img/purge_purge_3.png" width="800"/>

* tray_point: purge 时 toolhead 停靠的坐标位置。
* eject_point: 当前版本无意义, 无需修改此配置。
* custom_before: purge 之前执行的 gcode 命令, 用于自定义动作
* custom_after: purge 完成后执行的 gcode 命令, 用于自定义动作

#### [mms brush]

<img src="../img/purge_brush.png" width="800"/>

移动 toolhead 将喷嘴移动到固定位置(brush刷子所在的位置), 在刷子上来回移动清理喷嘴。

* enable: 此配置不会禁用 `custom_before` 和 `custom_after` 命令, 所以我们可以通过此配置禁用默认的 brush 流程, 使用脚本实施自定义的`清理喷嘴`流程。
* z_raise: 执行 BRUSH, MMS_BRUSH, MMS_BRUSH_WIPE, 或 MMS_BRUSH_PECK 动作前, z 轴抬升的高度, 命令执行完成后 z 轴高度会恢复原始高度。

    此参数仅作用于手动执行 MMS_EJECT 命令。
    
    换料流程中的 brush 动作不会额外应用此参数抬升 z 轴，而是由`[mms swap]`中的参数统一抬升 z 轴
* fan

    必需要配置 [[fan]](https://www.klipper3d.org/Config_Reference.html#fan)

    * fan_cooldown_speed: 刷喷嘴前开启风扇的转速, 用于冷却喷嘴上残留的耗材, 便于后续使用brush刷子清理喷嘴。
    * fan_cooldown_wait: 风扇开启后等待`fan_cooldown_wait`秒来冷却耗材。
* wipe 刷喷嘴
    * wipe_points: 清理喷嘴时 toolhead 移动的坐标值 (brush刷子所在的坐标)
    * wipe_speed: 清理喷嘴时 toolhead 移动的速度
    * wipe_times: 清理喷嘴时 toolhead 在 wipe_points 之间来回移动的次数
* peck 将喷嘴在brush刷子上敲击几下进一步清理喷嘴。由于brush刷子需要与 toolhead 在z轴上一起抬升/下降, 所以此功能作用不明显, 推荐不启用。
    * peck_point: brush刷子正中心的坐标, 喷嘴停靠于此坐标, z轴上下移动进一步清理。
    * peck_speed: z轴上下移动的速度
    * peck_depth: z轴上下移动的高度
    * peck_times: z轴上下移动的次数
* custom_before: brush 之前执行的 gcode 命令, 用于自定义动作
* custom_after: brush 完成后执行的 gcode 命令, 用于自定义动作


### hardware/mms-slot.cfg

#### [mms slot xxx]

<img src="../img/slot.png" width="700"/>

* `① brightness`: 可配置 RGB 的亮度, 1.0 代表 100% 亮度
* `② autoload_enable`: 在 `base/mms-motion.cfg` 中使能 `[mms autoload]` 后, 对应的 slot 可以通过此单独"启用/禁用"自动进料。
* `③ substitute_with`: 在 `mms/mms.cfg` 中使能 `slot_substitute_enable` 后, 对应的 slot 需要设置此配置, `打印时`检测到此料槽耗材用尽(Inlet未触发)后, 会使用此配置中的料槽自动续料。

    例如图中的 slot0 的 substitute_with 设置为 1。那么`打印时` slot0 料槽的耗材用尽后会自动加载 slot1 的耗材继续打印。

