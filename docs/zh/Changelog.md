# MMS 更新日志

## Ver 0.1.0496

- 挂起的换料请求不再阻断独立归零。重新归零或释放电机后，RESUME 拒绝使用旧返回点。手动任务提示使用 CLEAR_PAUSE，打印任务提示使用 CANCEL_PRINT。需要运动的手动 Tx 在未归零时直接提示 G28。
- 切刀回程状态在清除暂停或取消打印后仍有效。回程未完成时，配套本机宏禁止停车、归零、调平和擦嘴，取消打印仍会关热并跳过自动退料。XYZ 仍可信时，可用 MMS_SIMPLE_CUT 单独完成回程。
- 新增 `MMS_PAUSE_COOL`，记录暂停超时实际关闭的喷嘴目标温度。允许自动回温且当前目标为零时，RESUME 恢复该温度一次，用户已设定的非零目标温度优先。
- 配套本机配置在暂停超时后保留热床和电机锁定。放弃已超时的手动暂停后关闭全部加热器，并在切刀已完成回程时释放电机。
- 本机 `TEST_VIVID` 每步检查目标槽的 Inlet 和待清残料状态，并在 Tx 返回后核对实际槽位。发生备用槽替换时立即停止循环。

## Ver 0.1.0495

- 打印中 Inlet 用尽时，通过后台步进队列释放送料齿轮，使 Extruder 可以继续牵引管中余料，避免退选阻塞打印命令。
- 如果此时 Entry 断料或请求换到另一物理槽，先在废料桶上方停靠，再等待退选完成。退选失败时保留原故障，供 RESUME 处理。

## Ver 0.1.0494

- 退料时可在切刀压住耗材后，让已选中的原槽与 Extruder 同步短回抽，再让喷头沿切刀路径返回。新增 `cut_hold_retract_distance` 和 `cut_hold_retract_speed`，默认距离为 0 mm。
- 退刀失败时保留已完成的短回抽，恢复时接续处理退刀。独立切刀命令仍只执行切刀动作。

## Ver 0.1.0493

- 统一编辑、RFID 读取及 Moonraker `lane_data` 同步使用的耗材资料格式，兼容已有记录。RFID 设置的槽位灯色保留至该槽退空。
- 槽位退空时清除耗材资料并保留送料距离校准。Moonraker 断开时，打印可继续，耗材资料的本地编辑会保留在当前 Klipper 会话中，供重连后同步。
- 新增 `MMS_CHANGE_TOOL TOOL=<槽号>`，供 G-code 预处理器调用。实际换料及回位继续由 Tx 执行。

## Ver 0.1.0492

- 已验收的打印料路在 Inlet 用尽后，停止追补缓存，等已排队送料结束后退选，由 Extruder 继续牵引管中余料。
- 普通 RESUME 回到模型 XY 后，将暂停回抽的补偿量和 `resume_extra_extrude_distance` 合并为一次连续挤出，再降回打印高度。

## Ver 0.1.0491

- Klipper 就绪时读取各槽选择开关，释放仍咬合的送料齿轮并复核开关状态。带料重启后，可在喷头加热后直接手动挤出或回抽。

## Ver 0.1.0490

- 在 `[mms]` 中新增可选的 `exit_sensor`，与 `entry_sensor` 一起配置挤出机前后两端的耗材开关。出口状态可通过 `MMS_STATUS` 查看，并用于装载验收。
- 若装载验收时仅缺少出口信号，RESUME 可在原槽料路仍可用时限程补送，出口触发即停止。补送上限沿用 `fill_extrude_distance`。

## Ver 0.1.0489

- 装载填充中 Entry 短暂释放时，若原槽仍具备送料条件，先限程重新送至 Entry，再完成剩余挤出行程。超过重试范围或料路异常时暂停以便处理。

## Ver 0.1.0488

- 装载填充使用缓存反馈跟随 E 运动。低位时补料，满位时停止追加正向送料。取消依赖 Outlet 释放的咬料探测及随后的自动回抽重试，保留装载后的验料钩子。

## Ver 0.1.0487

- 人工修复后，RESUME 根据当前 Inlet、Gate 和 Entry 检查原槽能否接续装载。保留仍有效的填充进度。下游料路受扰动后重新验证牵引。
- 按当前 Gate 和 Entry 状态处理用尽料尾。料路已空时跳过回抽，已退回且需要取走的残料标记为待清理。
- 缓存开关在定位结束后回弹时，使用剩余的本地重试重新定位。咬料检查中 Outlet 再次触发时，只使用剩余的验证挤出行程。

## Ver 0.1.0486

- 统一装载、退料和恢复的料路交接。保留已完成的填充和切料进度，标记需要人工取走的本槽残料，并让 POP 使用同一条退料流程。
- 缓存定位结束后复查两端开关。维护或显式 Drive 同步结束时，只恢复仍然有效的原送料路径。多 MCU 归零的超时设置限定于 MMS 限位。
- 按槽保存送料距离校准，测量失败时保留旧值。加强槽位自检的取消、料路验空、RFID 读卡器和宏参数检查。
- 修复烘干停止时的定时器清理，并允许扩展配置写入失败后重试。

## Ver 0.1.0485

- 料路正常的暂停先执行短回抽，再抬 Z 并停靠。回抽失败时仍执行停靠。
- 增加限频的持续低位诊断日志，记录缓存开关状态和送料步进数据。

## Ver 0.1.0484

- Inlet 在送料途中用尽且 Entry 尚未触发时，对可控的旧尾尝试限程退料。关闭自动续料时，退完后暂停等待人工补料。
- 增加专用于缓存送料的 `buffer_max_velocity` 和 `buffer_max_accel`，默认分别为 80 mm/s 和 120 mm/s²。两项限制仅作用于 MMS 电机。
- 回抽后通过限程送料重新找回 Entry，日志显示实际重试轮次。暂停时尽快抬 Z，新增 `resume_extra_extrude_distance`，用于普通 RESUME 回位后的额外挤出。

## Ver 0.1.0483

- 缓存随动使用 Extruder 的运动加速度。`speed_drive` 和 `accel_drive` 用于显式装卸料。
- 直接测量缓存低位开关的释放点。装载后通过 Outlet 释放检查 Extruder 咬料，失败时同步回抽后限次重试。

## Ver 0.1.0482

- 缓存低位开关持续触发时继续响应补料需求，满位开关保持触发时停止追加正向送料。用实时开关状态修正因打滑产生的余量估计偏差。
- 重新测量时允许在有效范围内更新缓存行程，丢弃无效的低位释放参考，并记录缓存反馈日志。

## Ver 0.1.0481

- 已建立料路的冲刷、暂停回抽和恢复补偿共用缓存控制。缓存准备复用有效校准值，并通过每个缓存的 `buffer_target_percentage` 设置工作余量，默认 50%。
- 在最后清理喷嘴之前准备送料余量，保留限程参考搜索。新增可在宏面板使用的 `MMS_UNSELECT_SLOT`，供暂停维修时停止送料并释放选择器。

## Ver 0.1.0480

- 修复空闲后重启送料时重复规划已过去的时间窗口，以及步进未排空便进入休眠的问题。
- 送料和 Extruder 共用原生运动边界及 Drip 清理。旧版 Klipper 的不可恢复步进压缩错误触发停机保护，避免在失效的队列上继续恢复。

## Ver 0.1.0479

- 归零步进执行完后重新读取限位状态，再判定运动结果并释放电机。缓存测量、填充、清空和打印准备共用限程搜索，允许以校准速度进行本地重试。
- 备用链接中的各槽统一使用已装载料尾的判定。已释放的旧尾可以继续使用，恢复缓存监控失败时保留已完成的换料阶段。

## Ver 0.1.0478

- 重复 Tx 解析到同一已装载的物理槽时，即使 Inlet 已用尽，也可继续使用已验收的旧尾直至 Entry 释放，跳过重复上料和冲刷。

## Ver 0.1.0477

- 缓存送料与 Extruder 步进共用 Klipper 原生运动时钟。复用已排队的 E 轨迹预测消耗，并预留制动余量。`speed_drive` 用作显式送料的默认速度。
- 普通暂停或空闲时手动挤出也可得到补料。持续低位时随 E 需求追补，E 静止时限制补料尝试次数。移除临时配置 `buffer_feed_margin` 和 `buffer_refill_timeout`。
- 保留送料启动失败的原始原因，维护交接前排空已提交步进。

## Ver 0.1.0476

- 换料和恢复共用上游送料、同步填充和装载验收流程。RESUME 保留已完成的阶段和原故障原因。
- 手动与 Drip 步进执行完成后再交接电机。使用带时间顺序的限位反馈刷新 Entry，避免延迟报告覆盖较新的状态。
- 可控的占用料路复用同一退料流程。统一缓存余量估算与送料交接，后台缓存运动失败时进入暂停和停靠流程。

## Ver 0.1.0475

- Tx 指向已正常装载的同一物理槽时，包括多个逻辑工具映射到同一槽的情况，跳过切料、退料、进料和冲刷。
- 换料和重装共用分阶段的装载与恢复流程。有待完成的后台送料时拒绝 RESUME，换料故障暂停期间允许释放或重选选择器以便维修。
- 支持在打印开始时通过 `MMS_PURGE` 指定冲刷参数，处理同步送料交接，并在零挤出距离时保留预清理。

## Ver 0.1.0474

- 槽位循环测试的轮次标题只显示测试进度。各槽分别报告 RFID 检查状态，区分该槽禁用 RFID 与超出配置轮次而跳过。

## Ver 0.1.0473

- 统一换料、断料续料、暂停与恢复流程。进料未到 Entry 或装载验收失败时，RESUME 从对应阶段重试。部分送料的退料及可执行的故障停靠走废料桶路径。
- 已装载槽的 Inlet 用尽时保留管中料尾，直到下一次换料或 Entry 释放。开启自动续料时清理旧料并尝试备用链，关闭时暂停并释放选择器，等待人工处理。
- 切刀支持可选数字或 ADC 反馈，并在 `MMS_STATUS` 显示状态。修复归零和运动提交失败的传播、步进交接及挤出距离浮点边界。
- 冲刷和清残料使用连续 E 轨迹，期间关闭模型风扇。支持冲刷前机械清理、擦嘴宏及普通暂停恢复的可选回抽与冲刷设置。
- 将 `MMS_LOAD_SLOT` 的退料与装料合并为一个可取消任务，保留宏面板按钮。打印结束统一处理退料、运动清理和加热器关闭。
- 新增 `slots_rfid_check_loops` 控制循环测试的 RFID 检查轮次，各槽同时受自身 `rfid_enable` 控制。修复自动上料提前结束读卡及旧烘干定时器影响新一轮加热的问题。

## Ver 0.1.0472

- 为 `MMS_CHARGE`、`MMS_EJECT`、`MMS_CUT`、`MMS_PURGE`、`MMS_BRUSH` 等手动命令增加执行条件检查。
- 清理旧版异步装卸料和步进代码。简化槽位元数据文件定位，修复 Moonraker 同步记录含非数字键时的过滤。
- 修复从槽 0 换料时未暂停原槽缓存监控的问题。

## Ver 0.1.0471

- 修正缓存弹簧行程测量边界，测量后同步更新容量和目标余量，并调整测量速度、加速度及 E 运动监控范围。
- 修正 Moonraker 同步成功时的日志级别，以及全部退料被取消时的槽位日志。

## Ver 0.1.0470

- 更新 Moonraker 槽位资料同步，增加单槽记录删除接口。Inlet 释放时清除本地槽位的耗材和送料距离记录，再通知 Moonraker 更新。

## Ver 0.1.0469

- 切刀和冲刷模块通过 `get_status()` 向 Web UI 的 MMS 面板提供切刀终点和废料桶坐标。

## Ver 0.1.0468

- 修复换料映射缺少 `filename` 时，初始化或显示映射可能报错的问题。

## Ver 0.1.0467

- 修复同步耗材颜色以及读取 Fake AFC 面板状态时引用未定义变量的问题。

## Ver 0.1.0466

- 新增 `print_finish_exec`，可选择打印完成后是否自动退料并释放选择器，默认启用。
- 完善 Fake AFC 面板兼容，提供槽位映射、续料槽和缓存信息。

## Ver 0.1.0465

- 加入 MMS 与 Moonraker 槽位资料的读取、写入和删除接口，支持 KlipperScreen 和 Fake AFC 对接。同步功能仍在开发中。

## Ver 0.1.0461

- 新增 Moonraker 适配器，将槽位资料同步从 MMS 主类移入适配器。同步功能仍在开发中。

## Ver 0.1.0460

- 开始接入 Moonraker 槽位资料同步，支持上报槽位耗材颜色和材质的变更。新增 `MMS_LOG` 命令。

## Ver 0.1.0459

- 修复 mms_brush 中 'custom_before' 重复执行的 bug

## Ver 0.1.0458

- MMS 合并 Klipper Main 分支代码，至 2026-05-20

## Ver 0.1.0455 ～ 0.1.0457

- MMS Extend（复数 ViViD 拓展方案）优化，从 JSON 读取配置一次性渲染方案变更为 Klipper 风格的 INI-CFG 读取并动态生成方案

## Ver 0.1.0454

- Delivery：增加 can_walk() 判断 MMS Drive/Selector 运动中则不允许后续操作的限制

## Ver 0.1.0453

- Stepper:Drive 进退料在距离校检后的分段行为，首段从固定距离运动改为和第二段一样使用 Homing

## Ver 0.1.0452

- 与旧版 KlipperScreen for ViViD/MMS Ver 0.2.0011 的兼容性问题修复

## Ver 0.1.0451

- Swap SLOT mapping 映射的变更方式，分离为单层变动和递归变动两类

## Ver 0.1.0450

- Delivery 开放 sprint_speed/sprint_accel 参数作为用户配置项于 mms-motion.cfg 内新增
  - 该速度/加速度配置用于短距离 deliver 运动和 Autoload 中
- SLOT 变更 calibrate 后 deliver distance 的日志输出格式，用于修复 KlipperScreen Console 中未能正确显示 ->/<- 的问题
- Resume 增加异常捕获，防止恢复过程中异常再次抛出后的系统 Shutdown 问题
- Eject 增加同步方案最后的成功/失败判断，防止特殊情况下退料失败但仍然进入后续 Charge 流程的问题

## Ver 0.1.0449

- RFID 修复单次直接读取函数未前置检查 enable 开关的问题

## Ver 0.1.0448

- SLOT 取消夹料修正距离的取绝对值操作
- Delivery MMS9/MMS8 恢复旧逻辑的 MMS_SLOTS_WALK/MMS_SLOTS_LOOP，MMS09/MMS08 则作为产线用 Dev 命令
- gcode_adapter 注册命令时，增加命令说明支持

## Ver 0.1.0446

- Delivery mms_slots_check()/MMS8 新增逻辑
  - 每个 SLOT 开始前启用该 SLOT 对应的 MFRC522 detect 是否有 Tag
    - 若有则将该 MFRC522 对应的相邻 SLOT 各自 safe_retract 并重试直至 Tag 未读取
    - 即开始前不让 Tag 处于被读取位置
  - 每个 SLOT 进料到 Entry/Outlet 触发后，若未读取到 RFID 则不直接抛出异常终止，而是退出至 Gate 外并重试

## Ver 0.1.0445

- GCode Adapter 增加对 GCode 命令执行的异常捕获
- SLOT 增加 format_deliver_distance() 函数组装并返回 MMS7 测量的距离数据
- MMS_STATUS/MMS0/MMS00 命令返回的 MMS 系统状态带距离数据
- Delivery 有距离校准后，当进料到 Outlet/Entry 触发时使用特殊移动方式:
  - 先移动测量距离，剩余距离再慢速 Homing
  - 余量距离来自退料到 Gate 外的 safe_retract

## Ver 0.1.0443 ~ 0.1.0444

- PeriodicTask 定时任务增加 timeout callback 流，方便任务超时后能通过回调通知业务模块
- MMS RFID 增加 handle timeout 机制，处理 detect/read 超时后的逻辑
- SLOT RFID 修正 duration 参数的传递问题，reset() 函数增加 MMS RFID 相关状态检查与控制
- Autoload 改为默认开启
- Charge/Eject 默认使用同步方案，旧异步方案所使用的配置项大量删除，新增同步方案所需配置项

## Ver 0.1.0441 ~ 0.1.0442

- Purge 默认 purge_speed 从 600 降至 300 mm/min
- Delivery 命令 MMS8 内含的 mms_slots_check() 函数逻辑变更，修正 Exception 抛出后 rfid_detect() 可能没有正常停止的错误
- SLOT/SLOT_RFID/MMS_RFID 新增 detect_only 相关模式
- 新增 MMS_RFID_RESET 命令

## Ver 0.1.0439 ~ 0.1.0440

- Purge 重新使用异步方案，避免堵头造成的啃料问题
- Config 中的 cfg 文件增加 TRSync/Neopixel 的 Klipper 全局参数配置

## Ver 0.1.0438

- Stepper Drive sync with Extruder 同步状态管理加强
- SLOT 距离校准命令新增别名 MMS_BOWDEN_CALIBRATION
- 实际 Deliver 运动中测算的值不覆盖原距离校准值，即实时动态校准逻辑取消
- 距离校准测试结果保留至文件 mms-slot-meta.json
- 增加校准数据手动实时变更工作流

## Ver 0.1.0436 ~ 0.1.0437

- 测试并处理 Charge/Purge 同步机制下啃料问题

## Ver 0.1.0435

- 优化同步方案的细节表现

## Ver 0.1.0432 ~ 0.1.0434

- Charge/Eject/Purge 使用同步方案替换原有复杂异步方案，简化 Buffer 相关操作

## Ver 0.1.0431

- Unselect 逻辑变更，增加 MMS_EJECT_UNSELECT 命令
- 新增同步方案下的 Charge/Eject/Purge 行为逻辑

## Ver 0.1.0429 ~ 0.1.0430

- 大幅重构 drive_deliver 相关逻辑，送料逻辑变更
- 由于相对偏低层的调度逻辑被更新，中间层的运动控制函数也同步大幅更新
- 变更原有 MMS_SLOTS_WALK 为距离校准功能，alias 为 MMS7

## Ver 0.1.0428

- Autoload/Preload 适配新的正反转动夹料逻辑，不再自行控制夹料，而是交给 MMS Delivery 进行

## Ver 0.1.0427

- SLOT Meta 删除原本 deliver_distance 按距离区分字段数据结构
- 改为使用 deliver_vector 字典作为数据存储，即以抽象向量的形式存储管理
- 对应新数据结构重写接口函数

## Ver 0.1.0426

- Buffer 删除 fill/clear/halfway 的 speed/accel 参数传入控制
- SLOT 增加对 meta 中用于 homing 的 deliver_distance 增删改接口，增加上下文管理器用于防止部分短距离逻辑覆盖测算好的正常值

## Ver 0.1.0425

- Delivery 适配新的动态距离进退料机制
- Buffer 动态距离支持

## Ver 0.1.0422 ~ 0.1.0424

- Delivery 动态测算并记录 SLOT Pin to Pin 的步进电机运动距离
- SLOT Meta 支持 CRUD 相关管理

## Ver 0.1.0421

- 新增 MMS Dryer 模块，负责集中管理舱温加热相关功能

## Ver 0.1.0419 ~ 0.1.0420

- 彻底删除 slot_meta 字典以及对应逻辑，改为使用新引入的 SLOT Meta 机制
- SLOT Meta 负责初始化、更新、管理 SLOTs 相关的基本属性

## Ver 0.1.0418

- Eject 取消终段的 MMS_UNSELECT
- Macro MMS_EJECT_SLOT 在 MMS_EJECT 后增加 MMS_UNSELECT

## Ver 0.1.0416 ~ 0.1.0417

- mms-extend.cfg 增加 Heater 相关支持
- 更新 Extend Config 组合方式，加载顺序、配置内容大量变更

## Ver 0.1.0415

- ChangeLog 补充

## Ver 0.1.414

- 打印结束时注册的回调从 mms_eject 改为 mms_eject_unselect，即增加 unselect 功能

## Ver 0.1.413

- Buffer 简化日志输出
- Stepper wait_idle() 控制删除，is_running 软锁控制机制变更
- Observer 新增 StepperInfo，在 Observer 中注册/管理 mms stepper 信息
- Observer 新增 StepperRunningManager，负责在观察循环里检查注册 MMS 电机的运动状态，判断步进电机是否实际运动中（理论上同样可以用于判断是否抱死）

## Ver 0.1.412

- Buffer/Delivery 优化日志输出
- Delivery 增加管理调度定向定距离移动的统一方法，其他模块（如 Purge）对应逻辑更新

## Ver 0.1.411

- 修复 Delivery wait stepper idle 相关 bug

## Ver 0.1.410

- Selector refine calibration distance 可配置化
- Endless Spool 未开启时逻辑更新

## Ver 0.1.409

- MMS Stepper running/wait_idle/terminate 机制迭代
- SLOT/SLOT_Pin Homing terminal 相关逻辑抽象至各模块自身职能范围内

## Ver 0.1.408

- MMS Buffer feed/retract 小规模重构
- 继续增加运动调度过程中对 distance/speed/accel 三个关键运动参数的强校验步骤
- 修复非法运动参数导致 ManualMove.end_print_time 被错误赋空值导致的数学计算错误问题

## Ver 0.1.407

- 修复 MMS Stepper ManualMove 运动方式在小距离移动调度时距离丢失的问题

## Ver 0.1.406

- Delivery/Stepper 增加对 speed/accel 等运动参数的校验，防止非法负值/超大值/超小值的传入
- 修正日志拼写错误

## Ver 0.1.405

- Delivery 调整 Stepper:Selector 固定距离移动相关参数
- Delivery 新增 MMS_TEST_SELECTOR，用于测试 Selector 移动的一致性
- Delivery 新增 MMS_TEST_SELECTOR_MEASURE，用于测量 Pin:Selector 的可用触发区间
- Stepper 增加运动结束后的距离监控日志输出

## Ver 0.1.404

- bigtreetech-mms/hardware/mms-stepper.cfg 配置变更
- Delivery Stepper:Selector 相关调度逻辑变更
- Stepper 调度核心参数 interval_time 采用差分策略配置

## Ver 0.1.403

- Stepper "Communication timeout during homing" 异常不再 Shutdown，改为上报业务层
- Charge 变更 Careful Load 失败的异常处理逻辑，以适配 Stepper 的变动
- Delivery Select SLOT 成功后终段的固定距离校准问题修复

## Ver 0.1.402

- 增强 MMS Pause/Resume 的状态管理
- 修复 Charge 失败暂停后用户手动停止打印，新打印任务中 Charge 再次失败后暂停被跳过的问题

## Ver 0.1.401

- MMS Buffer 增强扩展 VVD/Buffer 场景下的日志输出
- 修复 Extend 后多 Buffer 场景下，暂停恢复后 Monitor 错误监控 SLOT 导致送料异常问题

## Ver 0.1.400

- 新增 MMS_MAN 命令，自动记录并输出由 MMS 创建的原生命令

## Ver 0.1.0399

- MMS Brush 修复 Peck 未启用时仍会移动到 PeckPoint 的多余操作问题
- MMS_EJECT 命令终段加入 MMS_UNSELECT

## Ver 0.1.0398

- 修复 NeopixelAdapter 未正确使用 ReactorMutex 导致软重启时 OverFlowError 并 Shutdown 的错误

## Ver 0.1.0397

- 代码清理，格式规范化
- 文档补全

## Ver 0.1.0396

- MMS_DISABLE 增加 MMS_UNSELECT 步骤
- BaseAdapter 增加 risk_get() 模式
- FanAdapter 新增无 Fan/异名 Fan 支持

## Ver 0.1.0395

- Delivery Config 迭代为 core/config 的解析方式
- 新增 MMS_UNSELECT，使 Stepper:Selector 不锁定任何 SLOT 的耗材；该命令支持 Extend VVD
- 修复错误触发 Filament Detection 并在最后 Disable MMS Steppers 导致 TTC 的 Bug
- Filament Detection 修复被非进料 SLOT 触发问题
- FilamentDetection/EndlessSpool 原有为了解放 Selecting SLOT 的操作全部改为 MMS_UNSELECT
- MMS_STOP 增加等待电机运动结束的步骤
- Pause 取消在暂停时 Disable MMS Stepper 的操作
- Autoload/Preload 修复无选中 SLOT 时报错的问题

## Ver 0.1.0394

- ExtruderAdapter extruder_speed mm/min -> mm/s 统一处理
- NeopixelAdapter 参考旧版 Neopixel(klipper/extras/neopixel.py) 的更新机制，在 NeopixelAdapter 里使用 Mutex 锁管理更新，解决 Neopixel 频繁更新报错问题
- Delivery MMS_SLOTS_WALK 复归，变回单独命令；和 MMS_SLOTS_CHECK 不同逻辑

## Ver 0.1.0393

- Charge 迭代，修复失败后（重试前）退料期间的漫长等待问题
- ExtruderAdapter 增设 drip_extrude()/drip_retract() 函数

## Ver 0.1.0392

- PinsAdapter 修复 Pin 复合上拉和取反同时声明报错问题

## Ver 0.1.0391

- 修正 mms-heater.cfg 中的配置

## Ver 0.1.0388 ~ 0.1.0390

- Endless Spool 迭代调整

## Ver 0.1.0387

- Charge 迭代，流程调整为 Careful Charge 也包含在重试循环里面

## Ver 0.1.0386

- MMS log_status() 增加 charged_slot 的输出
- Exceptions 新增 Filament Detection/Endless Spool 用的异常
- Observer 新增 unregister_xxx_callback
- Endless Spool 补全异常逻辑和日志输出
- MMS PAUSE 新增暂停时 Disable Stepper Drive/Selector

## Ver 0.1.0385

- mms_charge.charged_slot 作为判断当前目标 SLOT 的最高优先级依据
- Endless Spool 模块迭代测试

## Ver 0.1.0384

- 删除 SLOT Substitute 模块
- 新增 Endless Spool 模块
- 修复 Extend 后扩展的 MMS SLOT 被重复登记至列表的问题

## Ver 0.1.0383

- MMS PAUSE/RESUME 增加记录/还原 Selecting SLOTs 功能

## Ver 0.1.0382

- Filament Detection 模块迭代

## Ver 0.1.0381

- 删除 Filament Fracture 相关逻辑
- 新增 Filament Detection 模块

## Ver 0.1.0380

- 优化 Klipper shutdown 时 MMS last_breath 的日志输出格式。

- Autoload/Preload：调整流程

- Extend + MMS Buffer：当扩展（Extend）ViViD配置接入MMS时，MMS会生成并管理多个MMS缓冲器（mms_buffer），导致缓冲器相关GCode命令重复注册，引发Klipper报错并关机；修复以上错误，为 MMS Buffer 相关命令新增可选参数“EXTEND”（整数类型，默认值0），该扩展编号（Extend Num）对应mms-extend.cfg中配置的编号（如[mms extend 2]中的“2”），支持用户动态配置（例：控制扩展编号为2的缓冲器，可执行命令MMS_BUFFER_MEASURE EXTEND=2 SLOT=4）；新增槽位编号（slot_num）归属校验（例：若[mms extend 2]配置slot: 4,5,6,7，则执行MMS_BUFFER_MEASURE EXTEND=2 SLOT=3会报错并返回）。

## Ver 0.1.0379

- 断料检测（Filament Fracture）：执行归零期间断料处理备选逻辑（handle_while_homing_alter）测试；需求方要求重新梳理断料检测相关逻辑，暂停使用handle_while_homing_alter，回滚至常规的回零期间断料处理逻辑（handle_while_homing）。

- Purge：当前用户使用弩式切刀（Crossbow）完成切割后，停靠至托盘（Tray）过程中，现有设计为先沿Y轴再沿X轴移动，易引发撞切刀问题；优化运动方向可选配置（默认Y轴优先，通过参数axis_first: Y控制），支持用户调整为X轴优先移动；可通过修改电机移动方向配置解决该问题，支持通过专用配置变量调整弩式切刀（Crossbow cutter）的轴移动顺序，指定X轴先于Y轴移动，修复因硬编码Y轴优先移动逻辑导致的切割后刀头全速撞回切刀的问题；提供mms-purge.cfg中的推荐配置 `axis_first:  X`， 从托盘点（tray_point）开始的轴移动顺序，可选值：'X'（X轴优先）、'Y'（Y轴优先）、'XY'（同步移动）。

## Ver 0.1.0378

- Config：新增可选点（OptionalPoint）；支持 Purge eject_point 这类可选（X,Y）坐标点配置。

- Purge-Tray Eject：将弹出点（eject_point）改为可选配置；启用该配置时，清洗（mms_purge）完成后执行托盘弹出（tray_eject），可执行命令MMS_TRAY_EJECT；未配置或注释该配置项时，不执行托盘弹出（tray_eject），不可执行命令MMS_TRAY_EJECT；配置文件mms-purge.cfg中的弹出点（eject_point）默认处于注释禁用状态。

- Charge：新增 charge 成功的 slot_num 记录功能；打印结束后自动执行 teardown 操作。

## 0.1.0377

- Careful Charge 测试调整。

- 配置文件路径从 "sample-bigtreetech-mms" 更改为 "bigtreetech-mms"。

## 0.1.0376

- Delivery MMS_D_TEST/MMS996 测试，强制启用断料检测，耗材装载到 Outlet，触发 Filament Fracture 后花园鳗探头，继续下一个 SLOT，循环 200 次。

## 0.1.0375

- Charge 新增 "careful charge" 功能，在 standard charge 之前尝试同时缓慢进料。

- 删除 MMS_SIMPLE_CHARGE。

## 0.1.0374

- RFID: 增强数据接口；SLOT 增加 get_status() 返回 RFID tag 数据。

- 新增 MMS_RFID_TRUNCATE SLOT=n。

## 0.1.0373

- Filament Fracture 修复 "Filament Fracture Disable" 后无效问题；在 mms_buffer 的 simple_move() 中增加检查步骤，避免禁用时强制调用 handle。

- handle_while_feeding 当 Purge is disabled 时，直接暂停并闪灯，不进行清空操作。

- RFID 新增 LED color cover 和 Write 功能；新命令: MMS_RFID_READ, MMS_RFID_WRITE, MMS_RFID_DETECT_DEV, MMS_RFID_READ_DEV, MMS_RFID_WRITE_DEV。

## 0.1.0372

- 优化了 heater 的命名。

## 0.1.0371

- 增强了 MMS_SWAP_MAPPING 命令。现在它只需要 SWAP_NUM 和 SLOT 参数，允许动态、实时地将逻辑工具（T代码）映射到物理槽位。映射关系在打印结束时自动重置。

## 0.1.0370

- 增加了一个对特定槽位状态（selector=1, inlet=0, gate=1, runout=0, outlet=1, entry=1）的检查，如果任何共享同一缓冲器的槽位处于此状态，则会暂停打印并阻止加载。

- 对 Swap、Eject、Charge 和 Purge 的自定义宏钩子进行了小幅改进。

## 0.1.0369

- 合并了 Alan 对 PinsAdapter.allow_multi_use_pin() 的修复，以正确处理以 ^ 和 ~ 为前缀的引脚。

## 0.1.0368

- 修改了 PinsAdapter.lookup_pin()，强制声明 can_pullup=True，以支持上拉电阻配置。

- 在执行 mms_pause() 之前添加了强制 G90（绝对定位）命令，以防止相对模式问题。

## 0.1.0367

- 减少了控制台日志噪音：步进电机状态和 last_breath 消息现在仅记录到文件。

- 添加了 MMS_MOVE_SLOT 命令。

- 为许多 Delivery 命令（例如 MMS_LOAD、MMS_MOVE）添加了 WAIT 参数，让用户可以选择同步（阻塞）或异步（非阻塞）执行。现在 SPEED 和 ACCEL 参数对于 MMS_MOVE 和 MMS_DRIP_MOVE 也是可选的。

## 0.1.0366

- 合并了 Alan 的提交并格式化代码。

- 为宏命令添加了输入验证和用户体验改进（控制台响应）。

- 默认禁用 SLOT 替换功能。

## 0.1.0363 ~ 0.1.0365

- 移除了 mms_cut 对 slot_num 的要求，允许在空槽位上切割。

- 添加了 MMS_SLOTS_CHECK 命令，用于加载并验证所有槽位的所有传感器。

- 添加了 MMS_SIMPLE_CHARGE 命令，这是一个不需要加热、门检测或工具头快照的简化版本。

- 更新了配置文件格式，采用更清晰、对齐的样式。

- 为 Mainsail/Fluidd 用户添加了一套用户友好的宏命令（例如 MMS CHECK、MMS LOAD、MMS DISABLE）。

## 0.1.0361 ~ 0.1.0362

- 修复了 Observer 模块中打印状态判断的一个错误，该错误导致行为异常。之前的更改已恢复。

- 修改了 _async_purge_feed() 逻辑，使用直接的 mms_drive.manual_move() 调用，而不是高级的 mms_delivery.mms_move()，以防止在 Purge 过程中发生协调失败。

- 讨论并规划了更以用户为中心的配置管理风格。

## 0.1.0358 ~ 0.1.0360

- 更新了 Observer 模块，增加了 idle_timeout 状态检查，以实现更准确的打印状态检测。

- 添加了 MMS_AUTOLOAD_ENABLE 和 MMS_AUTOLOAD_DISABLE 命令。

- 重构了示例配置文件（sample-bigtreetech-mms/）的目录层次结构和路径，将旧版本保留为 sample-bigtreetech-mms_dev。

## 0.1.0356 ~ 0.1.0357

- 临时禁用了适配器中 Neopixel 的错误消息。

- 分离了 Autoload 配置，移除了其独立距离设置，并与 Delivery 模块的参数统一。将 semi_autoload 重命名为 pre_load。

## 0.1.0354 ~ 0.1.0355

- 添加了 SLOT 替换功能，该功能仅在当前打印会话中有效。相应更新了配置文件。

- 在 Swap 模块中添加了一个功能，用于在打印期间动态更新槽位映射。

## 0.1.0351 ~ 0.1.0353

- 在缓冲器进料/回抽操作期间增加了线材断裂监控。增强了检测逻辑，加入了最大距离限制，以防止 Entry 传感器卡住时出现问题。模块在清理前会选择不同的槽位，以确保 Bowden 管中没有阻力。

## 0.1.0349 ~ 0.1.0350

- 从 Delivery 模块中提取了线材断裂检测功能，将其独立为一个模块。与其他模块的交互现在使用标准的 mms_* 流程，并且归位操作和进料操作在断裂后的处理逻辑不同。

## 0.1.0348

- 重构了 MMS 以使用新的配置解析系统。

- 重构了 SLOT 以使用新的配置系统，拆分出 SLOT_LED 和 SLOT_RFID 组件。

## 0.1.0347

- 修复了 0.1.0344 版本引入的一个错误，该错误可能导致打印开始后的第一个 Tn 命令跳过加载步骤，导致空挤。恢复为始终在相同槽位交换时进行清理。

- 由于新的缓冲器硬件修订版，曾短暂重新启用了依赖 buffer_runout 而不是 outlet 的 Charge 逻辑，但在有限测试后回滚了。

## 0.1.0345 ~ 0.1.0346

- 修复了 Delivery 和 Swap 模块中重复的 mms_pause() 调用，该问题可能导致 Klipper 错误。

- 修复了一个错误，该错误可能导致在线材在 Charge 过程中断裂后，恢复打印并再次失败时导致空挤（"空打"）。

- 由于某些打印模式（例如擦拭塔）下高频更新时的 TTC 风险，将缓冲器进料/回抽从 DripMove 回退到 ManualMove。新的打印过程中断裂处理功能正在开发中。

## 0.1.0344

- 优化了同槽位交换（Tn -> Tn）的 mms_swap 行为。

- 合并了 Alan 的 Heater 配置更新。

- 在 Delivery 的 handle_filament_fracture() 中添加了 slot_led.activate_blinking()，以触发受影响槽位的 LED 闪烁效果。

## 0.1.0343

- 在 Delivery._drive_deliver_to() 中添加了前进/后退检查：线材断裂检测现在仅在前进（加载）移动时运行，回抽时不运行。

- 加强了错误处理：任何 mms_buffer 操作（填充/清空/半程）失败都会中断 Swap/Charge/Purge 过程。

- 将 SlotPin 中断延迟从 0.5 秒减少到 0.1 秒。

- 使 Trigger/Release 日志在控制台中静默（仅记录到文件）。

## 0.1.0342

- 调整了日志输出格式和时间。

- 将引脚解析逻辑恢复为上游 Klipper Master 版本。

- 使用 motion_queuing.drip_update_timer() 重新实现了 DripMove 运动，并添加了终止功能。

- 在 Delivery 中添加了 drip_move_* 函数和 MMS_DRIP_MOVE 命令。更新了 MMS_STOP/mms_stop() 以支持中断 DripMove 运动。

## 0.1.0341

- 删除了 motion/dripload.py。

- 增强了 Load to Entry 重试逻辑：如果在任何时刻 Outlet 传感器被触发，则打印暂停并发出警告。

- 标准化了线材断裂处理：所有断裂都会触发卸载和暂停。

## 0.1.0340

- 注释掉了 Charge 中最后的 mms_buffer.halfway() 调用，以实现更平滑的交换。

- 更改了默认的 Delivery 参数：speed_drive: 80, accel_drive: 40。

- 更新了 RFID 开发计划；由于 Elegoo 缺乏 RFID 线材，已暂停。

## 0.1.0336 ~ 0.1.0339

- 合并了上游 Klipper Master 更新（提交 2e58023）。

- 优化了 SWAP 过程：

- - Eject: 如果启用了清理，在切割前后移动到托盘点。

- - Purge: 默认禁用 pressure_pulse_cleaning 并移除了最后的 mms_buffer.halfway()。

- - Brush: 默认禁用 Peck。

- - Swap: 将最后的 mms_buffer.halfway() 移动到 mms_brush 之前执行。

- 修复了一个错误，即在恢复失败后 Pause/Resume 会失败：现在在恢复失败 1 秒后，一个计时器会将 is_paused 标志重置为 True，允许后续的重试尝试。

- 通过修改 Swap 中的 Z 轴恢复策略，修复了多次 Pause/Resume 循环后 Z 轴归位问题。

## 0.1.0335

- 移除了许多 MMS 命令的打印限制，允许在打印期间使用，并添加了别名以防止意外触发 UI 命令：

- - 缓冲器命令：MMS_BUFFER_FILL/CLEAR/HALFWAY/ACTIVATE/DEACTIVATE/MEASURE。

- - 送料命令：MMS_SELECT->MMS_SELECT_U, MMS_LOAD->MMS_LOAD_U, MMS_POP->MMS_POP_U, MMS_PREPARE->MMS_PREPARE_U。MMS_UNLOAD/MOVE/UNSELECT 保持不变。

- - 交换命令：MMS_EJECT, MMS_CHARGE, MMS_PURGE, MMS_TRAY, MMS_TRAY_EJECT, MMS_CUT, MMS_BRUSH, MMS_BRUSH_WIPE, MMS_BRUSH_PECK。

- 大幅减少了控制台日志输出。关键日志（SLOT 引脚变化、警告/错误、用户宏、SWAP 映射、MMS 状态）保留，而详细过程日志仅定向到 mms.log 文件。

## 0.1.0333

- 尝试修改 Klipper 的 pins.py 以支持每个归位移动有多个限位开关，但实现在 C 语言层面失败，已搁置。

- 将 SLOT Pins 相关类重构到 core/slot_pin.py 中。更新了其他模块中的引用。

- 创建了 SLOTGateInvert 类，但由于归位功能被搁置而未实例化。

## 0.1.0332

- 调整了函数名称并更新了其他模块中的调用。

- 调整了 extend 模块中的成员注册。

- 改进了对 mms_resume 中 is_paused 标志的控制，以防止在 Pause/Resume 失败后出现"打印未暂停，恢复中止"的问题。

- 优化了 Eject 工作流程，对加载槽位进行优先级排序，提高了多槽位场景下的安全性。

- 删除了遗留的 mms_eject 流程代码。

## 0.1.0331

- 在 last_breath 日志中添加了 MMS 版本输出。

- 修改了 toolhead_adapter，使其恢复工具头快照并加热到目标温度时无需等待（wait=False）。

- 使 Delivery 步进电机状态等待更严格：即使异步任务，如果步进电机不空闲，也会阻塞并等待（5 秒超时）。

- 修复了一个错误，该错误导致在 Pause/Resume 流程中恢复重试失败后，mms_pause 标志被错误地清除。

## 0.1.0330

- 将 MFRC522 RFID 驱动程序从 Klipper 的 extras 目录移动到 mms/hardware 模块，移除了其通用配置加载功能，并专门针对 MMS 用途进行了集成。

## 0.1.0326 ~ 0.1.0328

- 修复了 mms_eject 异常处理中与错误槽位号引用相关的错误。

- 增强了 Pause/Resume 逻辑，通过更好地控制 is_paused 状态标志，防止重复提交恢复及由此产生的问题（如"移动超出范围"错误）。

## 0.1.0323 ~ 0.1.0325

- 优化了 Charge 重试逻辑，将嵌套重试次数从 9 次减少到 6 次。

- 引入了 ChargeFailedError，以在 Charge 失败时触发 LED 闪烁。

- 将多台测试机器更新到此版本。

## 0.1.0321 ~ 0.1.0322

- 修复了一个错误，该错误导致恢复并停用 LED 闪烁效果时，可能因计时器状态管理的变化而导致 Reactor 错误和 Klipper 关闭。

- 修订了 Eject 工作流程以提高安全性，特别是在处理多个已加载槽位和决定何时切割线材方面。引入了 EjectFailedError 以在失败时触发 LED 闪烁。

## 0.1.0320

- 集成了 Klipper 的运动队列系统，该系统使用单独的线程来管理每个步进电机，并由一个全局模块管理。

- 解决了挤出机/驱动电机运动阻塞的根本原因，该问题源于 ManualStepper.do_set_position() 中调用了 toolhead.flush_step_generation()。

- 修改了 SLOT LED 初始化，使其以延迟方式顺序点亮。

## 0.1.0316 ~ 0.1.0319

- 继续合并上游 Klipper 更新，重点关注新的运动队列系统，该系统替代了手动步进生成和冲刷。

- 移除了 DripMoveDispatch 和相关逻辑。

- 修复了 MMSStepper.reset_position()，使其正确更新 ManualStepper 的内部位置，解决了挤出机和驱动电机同时运动时的阻塞问题。

## 0.1.0313 ~ 0.1.0315

- 使 MMS 适应上游 Klipper 更新（提交 'b4c7cf4'），特别是 Klippy connect/ready 回调中的更改，该更改不再允许使用 reactor.pause()。

- 修改了 Autoload 启动延迟和 SLOT LED 初始化，以避免在回调中使用 pause。

- 更新了 Task.py 中的 PeriodicTask，以适应 Reactor 计时器管理的更改。

## 0.1.0312

- 进一步减少了控制台日志记录，仅将完整日志保存在 mms.log 中。

- 合并并适应了从 2025-08-12 到 2025-11-19 的上游 Klipper 更新（包括 MCU、Toolhead、运动队列和 Reactor 更改）。无需重新刷写固件。

## 0.1.0311

- 由于新方法可靠性问题，将 0.1.0310 版本中的 mms_charge._standard_charge() 逻辑回退为依赖 Outlet (PA5) 传感器。

## 0.1.0310

- 减少了控制台日志噪音；详细日志现在仅写入 mms.log 文件。

- 修改了 mms_charge 和 mms_buffer.halfway() 的逻辑，通过使用新的四相运动功能，减少对 Outlet (PA5) 传感器的依赖。

- 更新了 mms_buffer.halfway() 以使用 buffer_runout 传感器进行定位。

## 0.1.0306

- 更新了 Observer 逻辑，以监控 VirtualSD.must_pause_work 来更准确地检测打印暂停状态，解决了 PrintStats.state 的时序问题。

## 0.1.0304 ~ 0.1.0305

- 将 Pause/Resume 逻辑提取到专用的 MMS_PAUSE 和 MMS_RESUME 模块中，以集中管理并解决反复出现的问题。

- MMS_RESUME 拦截并管理标准恢复命令的执行。

## 0.1.0303

- 增强了温度控制：toolhead_adapter 现在在 Pause/Resume 期间保存和恢复目标温度。

- 解决了 Klipper 服务重启后，Pause 递归问题和 Adapters 模块内部数据持久性问题。

- 注释掉了 idle_timeout 回调管理器以解决相关问题。

## 0.1.0302

- 为 Swap、Eject、Charge、Purge 和 Brush 模块添加了用户宏钩子（*_CUSTOM_BEFORE/AFTER），允许用户脚本注入到标准工作流程中。

## 0.1.0301

- 在 Delivery/Stepper 中解耦了运动方向与传感器触发/释放状态，实现了所有四种运动组合（前进/后退到触发/释放）。

- 将 mms_buffer.clear() 改为使用 unload_to_buffer_runout()，而不是旧的 fill() + retract 方法。

- 简化了 measure_stroke() 的实现。

- 移除了遗留的 Selector 摆动代码。

## 0.1.0300

- 增强了 Toolhead 快照功能，以存储/恢复目标温度，并在恢复时使用非阻塞加热。

- 移除了 Purge 过程中对 toolhead_adapter.release_pressure 的调用，以实现更平滑的操作。

- 在 Stepper.enable() 中添加了对 stepper_enable.is_motor_enable() 的预检查，以防止可能的工具头卡顿。

## 0.1.0298 ~ 0.1.0299

- 在 Swap 失败时添加了手动 truncate_snapshot 调用，以防止工具头错误地移回打印点。

- 在 apply_nozzle_priming() 中为 Selector/Drive 步进电机添加了等待步骤，以防止在异步任务中跳过。

## 0.1.0295 ~ 0.1.0297

- 测试了 Pressure Pulse Cleaning 功能。

- 移除了根据缓冲器弹簧行程限制 apply_nozzle_priming 距离的安全逻辑；现在直接使用配置的值。

- 修复了 Swap 过程中 slot_from 为 None 时的安全检查逻辑。

- 通过改用步进电机事件（mms:stepper:running/idle）来触发，修复了 LED 闪烁效果停用的错误。

- 移除了所有与 Stepper Soft Stop 相关的代码和配置。

## 0.1.0291 ~ 0.1.0294

- 在 Purge 模块中添加了 Pressure Pulse Cleaning 功能，用于喷嘴维护和材料冲刷，可在切割或冷拉之前使用。

- 移除了 ParkPoint 功能及其相关代码和配置。

## 0.1.0290

- 实现了一个新的、类型安全的配置解析系统，为所有 *.cfg 文件提供了类继承结构。

- 标准化并增强了所有 MMS Swap 模块配置文件中的英文注释，以提高清晰度和文档质量。

## 0.1.0286

- 精简了 Swap 流程，清理了代码，并添加了全面的日志记录和注释。

- 在新的 Swap 模块（Cut、Eject、Charge、Purge、Brush、Swap）上进行了测试。

## 0.1.0285

- 重构了 Swap 模块，将其拆分为独立的、功能专注的组件：swap.py、charge.py、cut.py、eject.py、purge.py、brush.py 和 utils.py。

- 每个模块现在都提供特定于业务的检查、状态管理和异常处理。

## 0.1.0283

- 明确并构建了 Swap 流程，定义了每个步骤的启用/禁用条件：eject、cut、mms_delivery.unload、charge、purge 和 brush。

## 0.1.0282

- 重构了适配器中的 Toolhead 运动逻辑。将 XYZ 轴运动调度从 gcode_move_adapter 抽象到 toolhead_adapter 中。

- 添加了 Toolhead 快照功能，以保存/恢复 XYZ 位置、挤出机温度和风扇速度。

- 添加了可在 mms_clean、mms_purge 和 mms_swap 模块中访问的风扇冷却功能。

## 0.1.0281

- 完成了 Clean 模块功能的迭代：fan_cooldown、brush wipe/peck 和 retraction_compensation。

- 添加了（非运行状态的）wipe_cold（冷擦拭）逻辑。

## 0.1.0280

- 将 Purge 与 Clean 模块分离。Purge 现在处理内部清洁（热/冷清理、压力均衡）。Clean 处理外部清洁（刷子擦拭、托盘敲除）。

- Purge 模块引入了基于材料类型和颜色差异使用矩阵映射系统进行自适应清理长度计算。

- 针对有/无切割器、有/无清理托盘的不同设置实现了不同的清理策略。

- 增强了 brush_wipe 功能，增加了自适应计算、材料特定温度、滴落步骤、冷擦拭和多方向/啄击运动。

- 实现了 knock_off_blob 动作序列，用于从清理托盘弹出料滴。

## 0.1.0271 ~ 0.1.0273

- 更改了 Endstop Homing 错误的异常处理。之前，MMS Stepper 代码捕获了所有异常，默默地抑制了 HomingMove 产生的底层 CommandErrors。现在允许这些 CommandErrors 向上传播。

- 引入了 DeliveryCommandError 来包装原始的 CommandError 和堆栈跟踪，以便更好地调试。

- 如果 MMS 捕获到 CommandError，它现在会直接调用 Printer.emergency_stop 以实现更清晰的关机过程。

## 0.1.0270

- 增强了对外部 UI 的状态报告。迭代了 MMS 模块的 get_status(eventtime) 函数，以通过 Klipper/Moonraker 钩子集成数据。

- 整合并构建了来自 mms.slot_meta、mms_slot、mms_stepper 和 mms_buffer 对象的状态返回，供外部（主要是 KlipperScreen）使用。

## 0.1.0263

- 修复了可能发生的严重 TTC（工具头碰撞）问题，如果：

- - 缓冲器进料/回抽操作导致在 Delivery 模块中重新夹紧。

- - 交换操作（如 T*）在缓冲器进料/回抽操作完成之前启动。

- 策略：简化了缓冲器进料，使用基本的 Drive 运动，无需重新夹紧或线材断裂检测。在开始 Swap 之前，添加了等待步骤（最长 15 秒），等待之前的 Selector/Drive 操作完成。

## 0.1.0261 ~ 0.1.0262

- 增强了 MMS_Buffer 以支持跨多个 ViViD 单元的映射，绑定槽位和缓冲器。

- 添加了 MMS_BUFFER_MEASURE SLOT=n 命令用于手动行程校准。

- 移除了遗留的 Dripload 流程代码和旧的缓冲器模块文件。

- 修复了一个错误，该错误导致 Buffer 行程测量中的 move_backward() 在 Runout 引脚触发时没有按预期停止。

- 修复了一个错误，该错误导致如果中间进料步骤失败，清理不会中止并暂停打印，从而改进了 Clean/Swap 工作流程中的错误处理。

## 0.1.0260

- 添加了自动缓冲器弹簧行程测量和校准功能，该功能在每次启动后的第一次 T* 操作时运行。

- 将 DriploadButton/Dripbutton 重命名为 buffer_runout/runout 以反映其在新的 MMS_Buffer 系统中的新角色。更新了所有相关代码和配置引用。

- 完全禁用了遗留的 Dripload 功能（包括配置和代码）。

- 注意：需要固件更新。C 语言层面的 SoftStop() 函数已被移除，使得 ManualMove 操作再次无法通过 MMS_STOP 中断。

## 0.1.0256~0.1.0259

- 对缓冲器进料/回抽系统进行了性能分析，显示运行时间高度稳定。

- 移除了与 cmd_MMS_SWAP 相关的命令。

- 将 Clean-Purge 逻辑与 MMS_Buffer 解耦。Purge 现在拥有自己独立的线材进料逻辑，使用更简单的同步、分段方法，基于缓冲器弹簧行程以防止线材研磨。

- 通过小的快速回抽和缓冲器状态检查增强了 Purge 完成操作，确保安全。

## 0.1.0253~0.1.0255

- 将所有 Dripload 功能完全迁移到新的 MMS_Buffer 系统。

- 更新了对 absolute_extrude 设置的处理，并改进了传感器状态检查以防止错误的体积计算。

- 改进了打印期间的进料/回抽逻辑，显著减少了线材研磨事件。

- 在 Swap Purge 阶段添加了速度匹配逻辑，防止过度压缩。

- 更新了配置文件：将传感器逻辑更改为非反相，并调整了默认清理距离。

## 0.1.0252

- 添加了新命令：MMS_BUFFER_ACTIVATE 和 MMS_BUFFER_DEACTIVATE。

- 修复了一个主要问题，即长距离回抽会导致异常的高速回抽尝试。改为使用挤出机的实际位置而不是虚拟的 gcode_position 进行计算。

- 在 Charge 操作结束时添加了一个强制加载到 Outlet 传感器的步骤，以校准 MMS_Buffer 的最大容量。

## 0.1.0251

- 修复了 mms_swap.relax_spring() 中与反相传感器逻辑相关的错误。由于连续回抽策略使得弹簧放松变得不必要，该函数现已弃用。

- 继续改进 Neo Buffer 的数据采集，改用实时挤出机位置数据。

## 0.1.0250

- 重新设计了缓冲器弹簧进料系统，从复杂的弹簧压缩模型转向更简单的基于容量的"Neo Buffer"概念。

- 用更直接的 Reactor/PeriodicTask 机制取代了生产者-消费者队列模型，以提高稳定性和降低系统负载。

## 0.1.0245

- 正式实现了缓冲器弹簧进料功能。

- 引入了一种监控策略，使用挤出机运动数据实时计算弹簧压缩率。

- 基于实时挤出流量动态计算所需的进料距离、速度和加速度，以保持最佳的缓冲器状态。

- 实现了一个带有任务队列（FeedQueue）的生产者-消费者模型，用于管理进料请求。

- 包含安全机制：进料前检查 Outlet 状态，限制单个进料距离，并在传感器触发时清除队列。

## 0.1.0242

- 继续调查和测试关于工具头卡顿的问题，重点关注 soft_break() 和底层步进电机通信。

- 增强了 Dripload 启动检查：现在验证 Outlet 传感器状态和挤出机动作类型（挤出/回抽）。

- 开始基于实时挤出流量设计和开发动态缓冲器弹簧进料系统。

## 0.1.0241

- 修复了 MMS 工具头和挤出机速度错误地被打印速度/挤出因子（M220）缩放的问题。

- 调查并修复了打印过程中偶尔观察到的工具头卡顿问题。

- 移除了阻止 Dripload 在清理操作期间激活的限制。

## 0.1.0240

- 改进了 Swap 中的多阶段卸载逻辑，以解决偶尔发生的第一阶段失败问题。

- 将 relax_spring() 方法改为卸载直到 Drip (PA4) 传感器释放，而不是使用挤出。

- 调整了首次卸载阶段的初始挤出机回抽逻辑。

## 0.1.0234

- 将 service.py 重命名为 task.py。

- 优化了 Delivery 逻辑，防止在特定条件下重复执行安全回抽。

- 在代码层面拦截了 RESUME 命令，以确保恢复时执行缓存的 MMS 命令，修复了由第三方配置覆盖引起的问题。

## 0.1.0233

- 修复了 Stepper enable() 中一个潜在的 print_time 错误，解决了电机嗡鸣/无法移动的问题。

- 在 soft_stop() 中添加了对底层 MCU 查询异常的处理。

- 整合了 Delivery 模块中的空闲等待函数。

- 移除了阻止 Dripload 在暂停期间触发的限制。

## 0.1.0232

- 调整了配置加载顺序；MMS_SLOT/MMS_EXTEND 现在使用延迟初始化。

- 改进了 Dripload 距离校准功能。

- 修复了一个错误，该错误导致 Dripload 在 Eject 后失活，从而导致卸载失败。

- 默认禁用了 Selector 摆动功能。

## 0.1.0231

- 重大 Dripload 改进：引入了动态距离计算和校准，使用统计方法优化停止距离，减少急停造成的损坏。

- 修复了适配器中在配置保存和重启时发生的一个错误。

## 0.1.0230

- 继续调整代码结构，分离了 GlobalKlippy 和管理模块。

- 更改了 LED 和 Service 相关模块的初始化方法。

## 0.1.0225

- 重构了 MMS 模块目录，将其组织为 core、hardware、motion 等。

## 0.1.0224

- 清理了适配器中的重复代码，标准化了 lookup_object() 的使用，并纯化了业务类。

## 0.1.0223

- 实现了剩余的核心适配器（ForceMoveAdapter、StepperEnableAdapter、VirtualSDCardAdapter 等）。

- 将之前的 Assist 和 Observer 机制完全集成到适配器系统中。

## 0.1.0222

- 修复了适配器中全局 Printer 对象在重启后未正确更新的问题。

- 实现了 ExtruderAdapter、GCodeMoveAdapter 和 NeopixelAdapter 等适配器。

- 将业务类与 Klipper 配置依赖解耦。

## 0.1.0221

- 添加了关键的 PrinterAdapter 用于管理打印机对象和事件。

- 实现了 GCodeAdapter 和 FanAdapter，并更新了相关通信代码。

- 清理并移除了过时的工具函数。

## 0.1.0220

- 引入了适配器架构，用于抽象与 Klipper 模块的交互。

- 实现了 ToolheadAdapter 并迁移了其他模块中的相关逻辑。

## 0.1.0214

- 重构了 Observer 回调机制，并添加了 CallbackManager。

- 添加了一个新的 Exceptions 模块，用于集中和管理来自各个模块的异常，增强了异常链处理。

- 添加了 MMS_LED_EFFECT_TRUNCATE 命令，用于一次性清除所有 LED 效果。

- 为 Slots 添加了异常处理回调。

- 为失败的槽位操作添加了 LED 效果提醒。

- 在 Delivery 中添加了安全回抽功能，默认 50mm。

- 调整了 Autoload 逻辑以适应安全回抽。

## 0.1.0212

- 修复了 Delivery 模块中的变量命名问题。

- 重构了 Observer 模块，将其拆分为多个独立的观察者，并改进了状态判断机制。

## 0.1.0211

- 为 mms.get_slot() 添加了参数验证。

- 通过添加等待步骤优化了 Delivery 过程，防止操作冲突。

- 重构并优化了 Selector 摆动逻辑。

## 0.1.0210

- 添加了 Selector 摆动测试功能，使 Selector 在卸载阶段摆动（仅用于测试）。

- 支持通过 MMS_D_TEST 命令动态调整摆动参数。

## 0.1.0206

- 将 mms-slot.cfg 中的默认 Inlet 配置从机械开关更改为光电开关。

- 增强了 LED 错误处理；配置不匹配不再阻止启动，并向用户发出警报。

## 0.1.0205

- 标准化并清理了错误日志输出格式。

- 修复了线材断裂检测中的错误；添加了恢复后的自动 mms_load 以填充 Gate 和 Outlet 之间的间隙。

## 0.1.0203

- 在 Advanced Dripload 中添加了对线材断裂检测的支持。

- 使 Semi-Autoload 变为异步且可通过 MMS_STOP 中断。

- 在 mms_pop 期间临时禁用断裂检测以防止误触发。

- 改进了 Observer.is_printing() 逻辑。

- 区分了 Swap/Charge 和 Dripload 场景下的断裂处理。

## 0.1.0202

- 基于 Inlet 传感器，在 HomingMove 加载/卸载过程中实现了线材断裂检测。

## 0.1.0201

- 在 MoveDispatch 中用 completion.wait() 替换了 stepper.pause()。

- 将 Button 触发/释放回调改为 FIFO 双端队列。

- 使用上下文管理器增强了 SLOT 回调控制。

## 0.1.0200

- 实现了 Advanced Dripload，由 PA4 触发，由 PA5 停止。

- 修复了 semi_load_to_gate 参数中的一个错误，该错误导致 Semi-Autoload 无法正常工作。

## 0.1.0196

- 在 MMSStepper 和 ManualMoveDispatch 类中封装了电机控制逻辑，用于异步、可中断的运动终止。

- 注意：高频终止可能导致 stepcompress 错误；使用仅限于低频场景，如 Advanced Dripload。

## 0.1.0195

- 在 C 语言中实现了 Stepper Soft Stop (command_stepper_soft_stop())，需要为 ViViD 重新刷写固件。

- 更改了 Swap/Clean/Cut 模块中的风扇控制逻辑；仅在 mms_clean 期间控制风扇进行清理。

## 0.1.0192

- 在 MMS_STATUS 中添加了对 Dripload Pin 的监控，并更新了日志消息。

## 0.1.0191

- 基于新的电机停止逻辑，恢复了 Autoload 中断和 RFID 检测中断功能。

- 恢复了 MMS_STOP/MMS999 命令功能。

## 0.1.0190

- 实现了 Trsync Break Homing，使得 HomingMove 操作（例如用于线材断裂检测）可以紧急停止。

## 0.1.0184

- 修复了一个错误，该错误导致失败的 Swap Pause 可能引发递归 PAUSE 调用和错误的任务状态标志。

- 修复了从上游合并后 sensor_ads1220.c 中的编译错误。

## 0.1.0183

- 调整了配置：将单次 Dripload 距离从 15mm 更改为 10mm。

- 开始原型设计线材断裂检测。

## 0.1.0182

- 小幅重构了 Delivery 模块，减少了选择/送料函数中的冗余代码。

## 0.1.0181

- 修复了 Selector 中的一个错误，该错误导致选择后焦点未更新，可能导致 get_current_slot 判断错误。

## 0.1.0180

- 合并了上游 Klipper Master 更新（提交 3ef760c）。

## 0.1.0173

- 引入了 Extend 功能，支持多个 ViViD 单元以实现多色打印。

- 更改了 RESUME 宏的覆盖方法以提高可靠性。

## 0.1.0160

- 重构了 Stepper 模块，整合了运动调度逻辑。将 MOVE/MOVE_TO/MOVE_UNTIL 合并为 DRIP_MOVE 类型。

- 在打印结束时添加了自动 MMS_EJECT。

- 更改了示例配置中驱动步进电机的 gear_ratio。

## 0.1.0155

- 小幅重构了 EJECT、CHARGE 和 SWAP 逻辑。

- 修复了一个错误，该错误导致 MMS_CHARGE 在加载失败后没有停止。

- 在打印结束时（完成/错误/取消）添加了自动 MMS_EJECT。

## 0.1.0152

- 将 _deliver_distance() 中的驱动调度从 move() 改为 manual_move()。

- 小幅重构了 EJECT 和 SWAP 逻辑。

## 0.1.0150

- 添加了 Semi-Autoload 功能及 MMS_SEMI_AUTOLOAD 命令。

- 在 Delivery 中添加了将移动距离作为外部参数传递的能力。

## 0.1.0143

- 将 Gear（现 Drive）步进电机更改为使用 Klipper 原生的 manual_home 进行传感器触发的移动，减少了延迟和线材压碎。

- 将 Outlet 传感器引脚更改为 buffer:PA5（更接近物理极限）。

- 将 Dripload 监控引脚与 Outlet 解耦，使用专用引脚（例如 !buffer:PA4）。

- 更新了命名约定：缓冲器传感器 -> Gate，齿轮步进电机 -> 驱动步进电机。

## 0.1.0111

- 修复了一个错误，该错误导致 Entry 传感器触发时没有停止 MMS_LOAD 命令。

- 修复了一个初始化错误，该错误导致在 get_status 中引用 mms_selector 时其尚未就绪。

## 0.1.0110

- 重构了 SLOT 模块，解耦了引脚状态管理、LED 控制、Autoload 和 RFID 逻辑。

- 添加了对 Entry 传感器的可选支持；如果配置了，加载逻辑优先使用 Entry 而不是 Outlet。

- 添加了暂停/恢复交换恢复：如果打印在交换期间暂停，恢复时将重试最后一个交换命令。

- 移除了对暂停期间执行 Delivery 操作（例如 MMS_LOAD、MMS_MOVE）的限制。
