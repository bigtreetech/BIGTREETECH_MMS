# MMS 更新日志

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
