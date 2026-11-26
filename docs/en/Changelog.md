# MMS Change Log

## Ver 0.1.0496

- Allow standalone homing while a tool change is pending. RESUME rejects a saved return position after rehoming or motor release. Use CLEAR_PAUSE to discard a manual request or CANCEL_PRINT to end a print. Manual Tx commands that need motion prompt for G28 if XYZ is unhomed.
- Keep cutter-return protection after clearing a pause or cancelling a print. While the return is pending, the matching printer macros prevent parking, homing, Z tilt, and wiping. Cancelling still turns off the heaters and skips automatic unload. If XYZ coordinates are still trusted, MMS_SIMPLE_CUT can finish the return by itself.
- Added `MMS_PAUSE_COOL` to record the nozzle target temperature when a pause times out and turns off the heater. RESUME restores that target once when reheating is enabled and the current target is zero. A nonzero temperature target set by the user takes precedence.
- The matching printer configuration keeps the bed heated and the motors enabled after a pause timeout. Discarding a timed-out manual pause turns off all heaters and releases the motors once the cutter return is complete.
- The printer’s `TEST_VIVID` checks the target slot’s Inlet and remnant status before each Tx and verifies the physical slot afterward. If the tool change used a backup slot, the loop stops immediately.

## Ver 0.1.0495

- Release the feeder through Klipper’s background step queue when Inlet runs out during a print, so the extruder can keep pulling the remaining filament without blocking print commands.
- If Entry runs out or a tool change requests a different physical slot during release, park over the waste tray before waiting for release to finish. Preserve release failures for RESUME.

## Ver 0.1.0494

- During ejection, optionally retract the selected source slot and the extruder together briefly while the cutter is held down, then return the toolhead along the cutter path. Added `cut_hold_retract_distance` and `cut_hold_retract_speed`, with the distance defaulting to 0 mm.
- Preserve the completed pull if the cutter return fails so recovery can resume the return. Standalone cut commands still perform only the cutting motion.

## Ver 0.1.0493

- Use the same filament metadata format for edits, RFID reads, and Moonraker `lane_data` synchronization, with compatibility for existing records. Retain the RFID-set slot LED color until the slot is empty.
- Clear filament metadata when the slot becomes empty and retain its delivery calibration. Printing can continue while Moonraker is disconnected, and local edits remain pending in the current Klipper session for synchronization after reconnection.
- Added `MMS_CHANGE_TOOL TOOL=<slot_number>` for the G-code preprocessor. Tx continues to handle the tool change and return move.

## Ver 0.1.0492

- When Inlet runs out on an accepted print path, stop topping up the buffer, finish queued feeder motion, and release the selector so the extruder can pull the remaining filament.
- On ordinary RESUME, combine repayment of the pause retraction with `resume_extra_extrude_distance` into one continuous extrusion after the XY return and before lowering to print height.

## Ver 0.1.0491

- At Klipper startup, read the slot selector switches, release any clamped feeder, and verify the switch states. After a restart with filament loaded, manual extrusion or retraction can proceed once the hotend is heated.

## Ver 0.1.0490

- Added the optional `exit_sensor` in `[mms]`, alongside `entry_sensor`, for filament sensing before and after the extruder drive. `MMS_STATUS` reports the exit state, and charge acceptance checks it.
- If the exit signal alone is missing at charge acceptance, RESUME can advance filament from the original slot while its feed path is usable. The move stops when Exit triggers and is bounded by `fill_extrude_distance`.

## Ver 0.1.0489

- If Entry briefly releases during charge filling while the original slot can still feed, use a bounded feed move to reacquire Entry, then complete the remaining extrusion. Pause for repair if retries are exhausted or the path becomes invalid.

## Ver 0.1.0488

- During charge filling, follow E motion using buffer feedback. Refill at low and stop adding forward feed at full. Removed the grip probe based on Outlet release and its automatic retract-and-retry sequence. The post-charge verification hook remains available.

## Ver 0.1.0487

- After manual repair, RESUME checks the original slot’s current Inlet, Gate, and Entry to determine where loading can continue. It retains fill progress that is still valid. Recheck traction if the downstream path was disturbed.
- Use the current Gate and Entry states when handling an exhausted tail. Skip withdrawal when the path is already clear and flag a withdrawn remnant when it needs to be removed.
- If a buffer switch rebounds after positioning, use the remaining local attempts to recover the reference. If Outlet triggers again during a grip check, use only the remaining verification travel.

## Ver 0.1.0486

- Unify feed-path handoff across loading, ejection, and recovery. Keep completed fill and cut progress, flag local remnants that require removal, and use the same withdrawal workflow for POP.
- Recheck both buffer switches after positioning. After maintenance or explicit drive synchronization, restore the original feed only if its path is still valid. Scope the multi-MCU homing timeout to MMS endstops.
- Save delivery calibration per slot and preserve the previous value on measurement failure. Improve cancellation, path-clearance, RFID reader, and macro parameter checks for slot diagnostics.
- Clean up dryer timers when heating stops and allow extension setup to be retried after a configuration write failure.

## Ver 0.1.0485

- For a pause with a healthy feed path, retract briefly before lifting Z and parking. Continue parking if retraction fails.
- Added rate-limited diagnostics for sustained low buffer reserve, including switch states and feeder step data.

## Ver 0.1.0484

- If Inlet runs out during feeding before Entry triggers, attempt bounded withdrawal of a controllable tail. With endless spool disabled, pause for manual refill after withdrawal.
- Added `buffer_max_velocity` and `buffer_max_accel` for buffer feeding, defaulting to 80 mm/s and 120 mm/s². These limits apply to the MMS motor only.
- Reacquire Entry with a bounded feed after retraction and log the actual retry count. Lift Z promptly on pause and add `resume_extra_extrude_distance` for extra extrusion after the return on ordinary RESUME.

## Ver 0.1.0483

- Use the extruder’s motion acceleration for buffer following. Use `speed_drive` and `accel_drive` for explicit loading and unloading.
- Calibrate the low-switch release edge directly. Check extruder grip after loading by observing Outlet release, with bounded retries after synchronized withdrawal.

## Ver 0.1.0482

- Keep responding to refill demand while the low switch remains active and stop adding forward feed while the full switch remains active. Use live switch states to correct reserve estimates after slippage.
- Update the measured buffer stroke within the valid range, discard invalid low-release references, and log buffer feedback.

## Ver 0.1.0481

- Use the buffer controller for purging, pause retraction, and resume repayment on an established path. Reuse valid calibration when preparing the buffer and configure its working reserve with `buffer_target_percentage`, defaulting to 50%.
- Prepare feed reserve before the final nozzle cleaning and retain bounded reference searches. Added `MMS_UNSELECT_SLOT` to the macro panel to stop feeding and release the selector during paused repairs.

## Ver 0.1.0480

- Fixed feeder restarts after idle so they skip elapsed motion windows and drain queued steps before going to sleep.
- Share native motion boundaries and Drip cleanup across feeder and extruder operations. Treat unrecoverable step-compression errors on older Klipper cores as shutdown faults to prevent recovery on an invalid queue.

## Ver 0.1.0479

- Recheck endstop states after homing steps finish before accepting the result and handing off the motor. Buffer measurement, filling, clearing, and print preparation share bounded reference searches with local retries at calibration speed.
- Apply the same loaded-tail rules to every slot in a backup chain. A released tail can remain in use, and failures restarting buffer monitoring preserve completed tool-change stages.

## Ver 0.1.0478

- If repeated Tx commands resolve to the same loaded physical slot after Inlet runs out, keep using the accepted tail until Entry releases and skip redundant loading and purging.

## Ver 0.1.0477

- Run buffer feeding and extruder stepping on Klipper’s native motion clock. Reuse queued E trajectories to estimate consumption and retain braking room. Use `speed_drive` as the default for explicit delivery.
- Supply manual extrusion while ordinarily paused or idle. At sustained low reserve, refill according to E demand and limit refill attempts while E is stationary. Removed the interim `buffer_feed_margin` and `buffer_refill_timeout` options.
- Preserve the original cause of feeder startup failures and drain committed steps before handing off to maintenance.

## Ver 0.1.0476

- Share upstream feeding, synchronized filling, and charge acceptance between tool changes and recovery. RESUME retains completed stages and the original fault.
- Hand off motors after manual and Drip steps finish executing. Refresh Entry with timestamped endstop feedback so delayed reports cannot overwrite newer states.
- Use one ejection workflow for controllable occupied paths. Coordinate buffer reserve and feeder handoff, and route background buffer motion failures through pause and parking.

## Ver 0.1.0475

- When Tx resolves to the same healthy loaded physical slot, including logical tools mapped to that slot, skip cutting, unloading, loading, and purging.
- Share staged charge and recovery between tool changes and reloads. Block RESUME while background delivery is pending and allow selector release or reselection during a paused tool-change repair.
- Support parameterized startup purging with `MMS_PURGE`, handle synchronized feeder handoff, and retain pre-cleaning when the extrusion distance is zero.

## Ver 0.1.0474

- Show only progress in slot-test round headers. Report RFID status per slot, distinguishing a slot with RFID disabled from one skipped by the round limit.

## Ver 0.1.0473

- Unify tool changes, runout handling, pause, and recovery. RESUME retries the relevant stage when feeding stops before Entry or charge acceptance fails. Partial-feed ejection and fault parking use the waste-tray path when available.
- Keep the loaded tail in use after Inlet runs out until the next tool change or Entry release. With endless spool enabled, clear the tail and try the backup chain. With it disabled, pause and release the selector for manual repair.
- Add optional digital or ADC cutter feedback to `MMS_STATUS`. Fix propagation of homing and motion dispatch failures, stepper handoff, and extrusion distance rounding at motion limits.
- Use continuous E trajectories for purging and clearing remnants, with the part fan off during extrusion. Support mechanical pre-cleaning, a nozzle wiping macro, and optional retraction and purging for ordinary pause and resume.
- Run unloading and loading for `MMS_LOAD_SLOT` as one cancellable task and keep its macro-panel button. Coordinate unloading, motion cleanup, and heater shutdown at print completion.
- Added `slots_rfid_check_loops` to control RFID test rounds, with each slot also governed by its own `rfid_enable` setting. Fixed autoload ending RFID reads early and stale dryer timers interrupting a later heating cycle.

## Ver 0.1.0472

- Added execution guards for manual commands including `MMS_CHARGE`, `MMS_EJECT`, `MMS_CUT`, `MMS_PURGE`, and `MMS_BRUSH`.
- Removed legacy asynchronous loading, unloading, and stepper code. Simplified slot metadata file lookup and fixed Moonraker record filtering when non-numeric keys are present.
- Fixed a tool change from slot 0 failing to pause the source buffer monitor.

## Ver 0.1.0471

- Corrected buffer spring-stroke bounds, refreshed capacity and target reserve after measurement, and adjusted calibration speed, acceleration, and the E monitoring range.
- Corrected the log level for successful Moonraker synchronization and the slot log when unloading all slots is cancelled.

## Ver 0.1.0470

- Updated Moonraker slot synchronization and added an endpoint to delete one slot record. When Inlet releases, clear local filament and delivery-distance records for the slot and notify Moonraker.

## Ver 0.1.0469

- Expose the cutter endpoint and waste-tray coordinates through `get_status()` for the MMS panel in the Web UI.

## Ver 0.1.0468

- Fixed errors initializing or displaying a tool-change mapping when `filename` is missing.

## Ver 0.1.0467

- Fixed undefined variable references when synchronizing filament color or reading Fake AFC panel status.

## Ver 0.1.0466

- Added `print_finish_exec` to control automatic ejection and selector release after printing. Enabled by default.
- Improved Fake AFC panel compatibility with slot mapping, backup-lane, and buffer information.

## Ver 0.1.0465

- Added interfaces to read, write, and delete MMS slot data in Moonraker for KlipperScreen and Fake AFC integration. Synchronization remained in development.

## Ver 0.1.0461

- Added a Moonraker adapter and moved slot data synchronization out of the main MMS class. Synchronization remained in development.

## Ver 0.1.0460

- Started integrating Moonraker slot data synchronization, including reporting changes to filament color and material. Added the `MMS_LOG` command.

## Ver 0.1.0459

- Fixed the bug where 'custom_before' was executed repeatedly in mms_brush

## Ver 0.1.0458

- Merged Klipper Main branch code into MMS, up to 2026-05-20

## Ver 0.1.0455 ~ 0.1.0457

- Optimized MMS Extend (multiple ViViD extension solution); changed from one-time rendering via JSON configuration reading to Klipper-style INI-CFG reading and dynamic generation

## Ver 0.1.0454
- Delivery: Added can_walk() check to restrict subsequent operations if MMS Drive/Selector is in motion

## Ver 0.1.0453

- Modified the segmented behavior of Stepper:Drive feed/retract after distance verification; the first segment changed from fixed-distance movement to Homing (same as the second segment)

## Ver 0.1.0452

- Fixed compatibility issues with the legacy KlipperScreen for ViViD/MMS Ver 0.2.0011

## Ver 0.1.0451

- Changed the modification method of Swap SLOT mapping, split into single-layer changes and iterater changes

## Ver 0.1.0450

- Exposed sprint_speed/sprint_accel parameters in Delivery as user-configurable items, newly added in mms-motion.cfg
  - These speed/accel configurations are used for short-distance deliver movements and Autoload
- Modified log output format of deliver distance after SLOT calibration to fix the issue where ->/<- could not be displayed correctly in KlipperScreen Console
- Added exception catching for Resume to prevent system Shutdown when exceptions are thrown again during recovery
- Added final success/failure judgment for the synchronous scheme in Eject to avoid the issue where unloading fails but the subsequent Charge process still runs under special conditions

## Ver 0.1.0449

- Fixed RFID issue where the direct single-read function did not check the enable switch in advance

## Ver 0.1.0448

- Removed absolute value operation for clamping correction distance in SLOT
- Restored legacy MMS_SLOTS_WALK/MMS_SLOTS_LOOP logic for Delivery MMS9/MMS8; MMS09/MMS08 are used as production-line Dev commands
- Added command description support when registering commands in gcode_adapter

## Ver 0.1.0446

- Added new logic for Delivery mms_slots_check()/MMS8
  - Enable MFRC522 detection for the corresponding SLOT to check for Tag presence before each SLOT starts
    - If a Tag is detected, perform safe_retract on the adjacent SLOTs corresponding to that MFRC522 and retry until no Tag is read
    - Ensure no Tag is in the read position before starting
  - After feeding each SLOT to the Entry/Outlet trigger, if no RFID is detected, do not throw an exception to abort directly; instead, retract outside the Gate and retry

## Ver 0.1.0445

- Added exception catching for GCode command execution in GCode Adapter
- Added format_deliver_distance() function in SLOT to assemble and return distance data measured by MMS7
- MMS system status returned by MMS_STATUS/MMS0/MMS00 commands now includes distance data
- After Delivery distance calibration, a special movement mode is used when feeding to Outlet/Entry trigger:
  - Move the measured distance first, then perform slow Homing for the remaining distance
  - The remaining distance comes from safe_retract when retracting outside the Gate

## Ver 0.1.0443 ~ 0.1.0444
- Added timeout callback stream for PeriodicTask timed tasks, enabling callback notifications to business modules upon task timeout
- Added handle timeout mechanism for MMS RFID to handle logic after detect/read timeout
- Fixed duration parameter passing issue in SLOT RFID; added MMS RFID-related status checks and control to the reset() function
- Set Autoload to enabled by default
- Charge/Eject now uses synchronous mode by default; most configuration items for the old asynchronous mode removed, new configuration items for synchronous mode added

## Ver 0.1.0441 ~ 0.1.0442
- Reduced default purge_speed for Purge from 600 to 300 mm/min
- Modified logic of the mms_slots_check() function included in Delivery command MMS8, fixed the error where rfid_detect() might not stop properly after an Exception is thrown
- Added detect_only related modes for SLOT/SLOT_RFID/MMS_RFID
- Added MMS_RFID_RESET command

## Ver 0.1.0439 ~ 0.1.0440

- Reverted Purge to asynchronous mode to avoid filament grinding caused by nozzle clogging
- Added global Klipper parameter configuration for TRSync/Neopixel in cfg files under Config

## Ver 0.1.0438

- Enhanced synchronization state management for Stepper Drive sync with Extruder
- Added alias MMS_BOWDEN_CALIBRATION for the SLOT distance calibration command
- Values calculated during actual Deliver movement no longer overwrite the original calibrated distance values; real-time dynamic calibration logic removed
- Saved distance calibration test results to file mms-slot-meta.json
- Added workflow for manual real-time modification of calibration data

## Ver 0.1.0436 ~ 0.1.0437

- Tested and addressed filament grinding under the Charge/Purge synchronization mechanism

## Ver 0.1.0435

- Optimized detailed behavior of the synchronization scheme

## Ver 0.1.0432 ~ 0.1.0434

- Replaced the original complex asynchronous scheme with synchronous mode for Charge/Eject/Purge, simplifying Buffer-related operations

## Ver 0.1.0431

- Modified Unselect logic, added MMS_EJECT_UNSELECT command
- Added Charge/Eject/Purge behavior logic under the synchronous scheme

## Ver 0.1.0429 ~ 0.1.0430

- Major refactoring of drive_deliver related logic and feeding logic
- Lower-level scheduling logic updated, with corresponding major updates to intermediate motion control functions
- Renamed original MMS_SLOTS_WALK to distance calibration function, alias set to MMS7

## Ver 0.1.0428

- Adapted Autoload/Preload to the new forward/reverse clamping logic; no longer controls clamping directly, delegating to MMS Delivery instead

## Ver 0.1.0427

- Removed distance-based field data structure for original deliver_distance from SLOT Meta
- Replaced with deliver_vector dictionary for data storage, managing in the form of abstract vectors
- Rewrote interface functions corresponding to the new data structure

## Ver 0.1.0426

- Removed speed/accel parameter passing control for fill/clear/halfway in Buffer
- Added CRUD interfaces for homing-related deliver_distance in SLOT meta; added context manager to prevent short-distance logic from overwriting calibrated normal values

## Ver 0.1.0425

- Adapted Delivery to the new dynamic distance feed/retract mechanism
- Added dynamic distance support for Buffer

## Ver 0.1.0422 ~ 0.1.0424

- Dynamically calculated and recorded stepper motor movement distance for SLOT Pin to Pin in Delivery
- Enabled SLOT Meta CRUD management

## Ver 0.1.0421

- Added new MMS Dryer module for centralized management of chamber heating-related functions

## Ver 0.1.0419 ~ 0.1.0420

- Completely removed the slot_meta dictionary and corresponding logic, replaced with the newly introduced SLOT Meta mechanism
- SLOT Meta responsible for initializing, updating, and managing basic properties related to SLOTs

## Ver 0.1.0418

- Removed final MMS_UNSELECT step from Eject
- Macro MMS_EJECT_SLOT now appends MMS_UNSELECT after MMS_EJECT

## Ver 0.1.0416 ~ 0.1.0417

- Added Heater-related support in mms-extend.cfg
- Updated Extend Config composition method with major changes to loading order and configuration content

## Ver 0.1.415

- ChangeLog supplement

## Ver 0.1.414

- The callback registered at the 'finish' state of printing has been changed from mms_eject to mms_eject_unselect, adding the unselect function.

## Ver 0.1.413

- Simplified log output for MMS Buffer
- Removed Stepper wait_idle() control and changed the 'is_running' soft lock control mechanism
- Added StepperInfo to Observer to register and manage mms stepper information in Observer
- Added StepperRunningManager to Observer, which is responsible for checking the motion status of registered MMS motors in the observation loop and judging whether the stepper motor is actually in motion (theoretically can also be used to judge whether it is stalled)

## Ver 0.1.412

- Optimized log output for Buffer/Delivery
- Added a unified method for managing and scheduling directional fixed-distance movement in Delivery, and updated the corresponding logic in other modules (such as Purge)

## Ver 0.1.411

- Fixed bugs related to Delivery wait stepper idle

## Ver 0.1.410

- Made Selector refine calibration distance configurable
- Updated logic when Endless Spool is disabled

## Ver 0.1.409

- Developed the running/wait_idle/terminate mechanism of MMS Stepper
- Abstracted the logic related to SLOT/SLOT_Pin Homing terminal into the respective functional scope of each module

## Ver 0.1.408

- Minor refactoring of MMS Buffer feed/retract
- Continued to add strict validation steps for the three key motion parameters (distance/speed/accel) during motion scheduling
- Fixed the mathematical calculation error caused by illegal motion parameters leading to the incorrect null assignment of ManualMove.end_print_time

## Ver 0.1.407

- Fixed the distance loss issue of MMS Stepper ManualMove motion mode during small-distance movement scheduling

## Ver 0.1.406

- Added validation for motion parameters such as speed/accel in Delivery/Stepper to prevent the input of illegal negative values, excessively large values and excessively small values
- Corrected typos in logs

## Ver 0.1.405

- Delivery update relevant parameters for Stepper:Selector fixed-distance movement
- Delivery added MMS_TEST_SELECTOR for testing the movement consistency of Selector
- Delivery added MMS_TEST_SELECTOR_MEASURE for measuring the available trigger range of Pin:Selector
- Stepper added distance monitoring log output after motion completion

## Ver 0.1.404

- Configuration changes for bigtreetech-mms/hardware/mms-stepper.cfg
- Modified scheduling logic related to Delivery Stepper:Selector
- The core scheduling parameter `interval_time` of Stepper is configured with a differential strategy

## Ver 0.1.403

- The "Communication timeout during homing" exception of Stepper no longer triggers Shutdown, and instead reports to the business layer
- Modified the exception handling logic for Careful Load failure in Charge to adapt to the changes of Stepper
- Fixed the fixed-distance calibration issue in the final phase after successful SLOT selection in Delivery

## Ver 0.1.402

- Enhanced state management for MMS Pause/Resume
- Fixed the issue where the pause was skipped after Charge failed again in a new print job when the user manually stopped printing after a Charge failure pause

## Ver 0.1.401

- Enhanced MMS Buffer to extend log output in VVD/Buffer bussiness
- Fixed the feeding abnormality caused by the Monitor incorrectly monitoring the SLOT after pause and resume in the multi-Buffer scenario following Extend

## Ver 0.1.400

- Added the MMS_MAN command to automatically record and output raw commands created by MMS

## Ver 0.1.0399

- MMS Brush fixed issue where unnecessary movement to PeckPoint still occurred when Peck was not enabled
- MMS_EJECT command end stage added MMS_UNSELECT

## Ver 0.1.0398

- Fixed error in NeopixelAdapter where ReactorMutex was not used correctly, causing OverFlowError and Shutdown during soft restart

## Ver 0.1.0397

- Code cleanup, format standardization
- Documentation completion

## Ver 0.1.0396

- MMS_DISABLE added MMS_UNSELECT command
- BaseAdapter added risk_get() mode
- FanAdapter added support for no "[fan]" or "[fan]" configured with a different section name

## Ver 0.1.0395

-   Delivery Config iteration to use core/config parsing style
-   Added MMS_UNSELECT, making Stepper:Selector not lock filament of any SLOT; this command supports Extend VVD
-   Bug Fixed: Filament Detection was incorrectly triggered and disable MMS Steppers, finally caused TTC
-   Filament Detection fixed issue of being triggered by non-feeding SLOT
-   Operations in FilamentDetection/EndlessSpool originally intended to release selecting SLOT all updated to MMS_UNSELECT
-   MMS_STOP added step to wait for motor movement to end
-   Pause removed the operation of disabling MMS Stepper when paused
-   Autoload/Preload fixed error log when no SLOT is selected

## Ver 0.1.0394

- ExtruderAdapter extruder_speed mm/min -> mm/s unified processing.
- NeopixelAdapter references the update mechanism of the old Neopixel (klipper/extras/neopixel.py), uses Mutex lock for management in NeopixelAdapter to resolve frequent Neopixel update errors.
- Delivery MMS_SLOTS_WALK homing, changed back to a separate command; different logic from MMS_SLOTS_CHECK.

## Ver 0.1.0393

- Charge iteration, fixed the long wait issue during retraction after a failure (before retry).
- ExtruderAdapter added drip_extrude()/drip_retract() functions.

## Ver 0.1.0392

- PinsAdapter fixed the error when declaring both Pin pull-up and inversion simultaneously.

## Ver 0.1.0391

- Corrected configuration in mms-heater.cfg.

## Ver 0.1.0388 ~ 0.1.0390

- Endless Spool iteration adjustments.

## Ver 0.1.0387

- Charge iteration, process adjusted to include Careful Charge within the retry loop.

## Ver 0.1.0386

- MMS log_status() added output for charged_slot.
- Exceptions added new exceptions for Filament Detection/Endless Spool
- Observer added unregister_xxx_callback.
- Endless Spool completed exception logic and log output.
- MMS PAUSE added disabling Stepper Drive/Selector when pausing.

## Ver 0.1.0385

- mms_charge.charged_slot used as the highest priority basis for judging the current target SLOT.
- Endless Spool module iteration testing.

## Ver 0.1.0384

- Deleted SLOT Substitute module.
- Added Endless Spool module.
- Fixed issue where expanded MMS SLOTs after Extend were repeatedly registered to the list.

## Ver 0.1.0383

- MMS PAUSE/RESUME added functionality to record/restore Selecting SLOTs.

## Ver 0.1.0382

- Filament Detection module iteration.

## Ver 0.1.0381

- Deleted Filament Fracture related logic.
- Added Filament Detection module.

## Ver 0.1.0380

- Optimized the log output format of MMS last_breath.

- Autoload/Preload: Adjusted the process - get ViViD of the same group for SLOT_new triggering Autoload/Preload, record SLOT_old clamped by current group ViViD Selector, switch to SLOT_new to execute Autoload (load_to_gate & unload_to_gate) or Preload (pre_load_to_gate & unload_to_gate, difference lies in whether Inlet trigger is needed), then switch back to SLOT_old.

- Extend + MMS Buffer: Added singleton BufferCommand to manage MMS Buffer-related GCode commands (MMS_BUFFER_ACTIVATE, MMS_BUFFER_DEACTIVATE, MMS_BUFFER_MEASURE, MMS_BUFFER_FILL, MMS_BUFFER_CLEAR, MMS_BUFFER_HALFWAY) to fix repeated registration and Klipper shutdown issue; added optional "EXTEND" parameter (INT, default 0) for the above commands to support dynamic configuration; added slot_num ownership verification.

## Ver 0.1.0379

- Filament Fracture: Conducted handle_while_homing_alter Test; suspended use of handle_while_homing_alter and rolled back to regular handle_while_homing as required to re-sort out relevant logic.

- Purge (Crossbow Cut & Tray Docking): Optimized optional movement directions (default Y-axis first via "axis_first: Y"); allowed users to adjust to X-axis first to fix cutter collision issue; provided recommended configuration in mms-purge.cfg; supported MMS_TRAY command for testing updated movement behavior.

## Ver 0.1.0378

- Config: Added OptionalPoint; supported optional (X,Y) coordinate configuration like Purge eject_point.

- Purge (Tray Eject): Changed eject_point to optional configuration (execute tray_eject and support MMS_TRAY_EJECT when enabled; not execute and not support when unconfigured/commented out); eject_point in mms-purge.cfg is disabled by comment by default.

- Charge: Added slot_num recording function for successful charge; automatically performed teardown after printing.

## Ver 0.1.0377

- Careful Charge test adjustments.

- Config file path changed from "sample-bigtreetech-mms" to "bigtreetech-mms".

## Ver 0.1.0376

- Delivery MMS_D_TEST/MMS996 test with forced filament fracture detection, filament load to outlet, triggers filament fracture sensor, proceeds to next SLOT, and loops 200 times.

## Ver 0.1.0375

- Charge added "careful charge" feature, which attempts slow feeding simultaneously before standard charge.

- Command 'MMS_SIMPLE_CHARGE' removed.

## Ver 0.1.0374

- Enhanced RFID data interface; SLOT now includes get_status() to return RFID tag data.

- Added MMS_RFID_TRUNCATE SLOT=n.

## Ver 0.1.0373

- Filament Fracture: Fixed issue where "Filament Fracture Disable" was ineffective; added check steps in mms_buffer's  simple_move() to prevent forced handle calls when disabled.

- handle_while_feeding: When purge is disabled, directly pauses and flashes light without performing purge operation.

- RFID: Added LED color cover and write functionality; new commands: MMS_RFID_READ, MMS_RFID_WRITE, MMS_RFID_DETECT_DEV, MMS_RFID_READ_DEV, MMS_RFID_WRITE_DEV.

## Ver 0.1.0372

- Optimized the naming of the heater.

## Ver 0.1.0371

- Enhanced the MMS_SWAP_MAPPING command. It now only requires SWAP_NUM and SLOT parameters, allowing for dynamic, real-time mapping of a logical tool (T-code) to a physical slot. Mappings are automatically reset at the end of the print.

## Ver 0.1.0370

- Added a check for a specific slot state (selector=1, inlet=0, gate=1, runout=0, outlet=1, entry=1) to pause the print and prevent loading if any SLOT sharing the same Buffer is in this state.

- Minor improvements to the Custom Macro hooks for Swap, Eject, Charge, and Purge.

## Ver 0.1.0369

- Merged Alan's fix to PinsAdapter.allow_multi_use_pin() to correctly handle pins prefixed with ^ and ~.

## Ver 0.1.0368

- Modified PinsAdapter.lookup_pin() to forcibly declare can_pullup=True, supporting pull-up resistor configurations.

- Added a forced G90 (absolute positioning) command before executing mms_pause() to prevent relative mode issues.

## Ver 0.1.0367

- Reduced console log noise: Stepper status and last breath messages are now file-only.

- Added the MMS_MOVE_SLOT command.

- Added a WAIT parameter to many Delivery commands (e.g., MMS_LOAD, MMS_MOVE) to let users choose between synchronous (blocking) and asynchronous (non-blocking) execution. The SPEED and ACCEL parameters are also now optional for MMS_MOVE and MMS_DRIP_MOVE.

## Ver 0.1.0366

- Merged Alan's commits and formatted the code.

- Added input validation and user experience improvements (console responses) for macro commands.

- Disabled SLOT substitution by default.

## Ver 0.1.0363 ~ 0.1.0365

- Removed the slot_num requirement for mms_cut, allowing cutting on an empty slot.

- Added the MMS_SLOTS_CHECK command to load and verify all sensors for all slots.

- Added the MMS_SIMPLE_CHARGE command, a simplified version that does not require heating, gate checks, or toolhead snapshots.

- Updated configuration file formatting to a cleaner, aligned style.

- Added a suite of user-friendly macro commands for Mainsail/Fluidd users (e.g., MMS CHECK, MMS LOAD, MMS DISABLE).

## Ver 0.1.0361 ~ 0.1.0362

- Fixed a bug in the Observer's print state judgment that caused incorrect behavior. The previous change was reverted.

- Modified the _async_purge_feed() logic to use direct mms_drive.manual_move() calls instead of the high-level mms_delivery.mms_move() to prevent coordination failures during Purge.

- Discussed and planned for a more user-centric configuration management style.

## Ver 0.1.0358 ~ 0.1.0360

- Updated the Observer module to include idle_timeout status checks for more accurate print state detection.

- Added MMS_AUTOLOAD_ENABLE and MMS_AUTOLOAD_DISABLE commands.

- Restructured the directory hierarchy and paths for the sample configuration files (sample-bigtreetech-mms/), keeping the old version as sample-bigtreetech-mms_dev.

## Ver 0.1.0356 ~ 0.1.0357

- Temporarily silenced Neopixel error messages in the Adapter.

- Separated Autoload configuration, removing its independent distance setting and unifying it with the Delivery module's parameters. Renamed semi_autoload to pre_load.

## Ver 0.1.0354 ~ 0.1.0355

- Added SLOT substitution feature, which is active only for the current print session. Updated configuration files accordingly.

- Added a function in the Swap module to dynamically update the slot mapping during a print.

## Ver 0.1.0351 ~ 0.1.0353

- Added filament fracture monitoring during Buffer Feed/Retract operations. Enhanced detection logic with a maximum distance limit to prevent issues with stuck Entry sensors. The module selects a different slot before purging to ensure no resistance in the Bowden tube.

## Ver 0.1.0349 ~ 0.1.0350

- Extracted Filament Fracture Detection from the Delivery module into a standalone module. Interaction with other modules now uses standard mms_* processes, with different post-fracture logic for homing vs. feeding operations.

## Ver 0.1.0348

- Refactored MMS to use a new configuration parsing system.

- Refactored SLOT to use the new config system, splitting out SLOT_LED and SLOT_RFID components.

## Ver 0.1.0347

- Fixed a bug introduced in 0.1.0344 where the first Tn command after starting a print might skip the load step, causing empty extrusion. Reverted to always purging on same-slot swaps.

- Briefly re-enabled Charge logic relying on buffer_runout instead of outlet due to a new Buffer hardware revision, but rolled back after limited testing.

## Ver 0.1.0345 ~ 0.1.0346

- Fixed duplicate mms_pause() calls in Delivery and Swap modules that could cause Klipper errors.

- Fixed a bug where, after a filament fracture during Charge, resuming and failing again could lead to empty extrusion ("air printing").

- Reverted Buffer Feed/Retract from DripMove back to ManualMove due to TTC risks under high-frequency updates during certain print patterns (e.g., wiping towers). New fracture handling during printing is under development.

## Ver 0.1.0344

- Optimized mms_swap behavior for same-slot swaps (Tn -> Tn).

- Merged Alan's Heater config updates.

- Added slot_led.activate_blinking() to handle_filament_fracture() in Delivery, triggering a blinking LED effect on the affected SLOT.

## Ver 0.1.0343

- Added forward/backward check in Delivery._drive_deliver_to(): Filament fracture detection now only runs during forward (load) moves, not during retracts.

- Tightened error handling: any mms_buffer operation (fill/clear/halfway) failure now interrupts the Swap/Charge/Purge process.

- Reduced SlotPin break delay from 0.5s to 0.1s.

- Made Trigger/Release logs silent in the console (file-only).

## Ver 0.1.0342

- Adjusted log output format and timing.

- Reverted Pin parsing logic to the upstream Klipper Master version.

- Re-implemented DripMove motion using motion_queuing.drip_update_timer(), adding a Terminate function.

- Added drip_move_* functions to Delivery and the MMS_DRIP_MOVE command. Updated MMS_STOP/mms_stop() to support interrupting DripMove motions.

## Ver 0.1.0341

- Deleted motion/dripload.py.

- Enhanced Load to Entry retry logic: if the Outlet sensor triggered at any point, the print pauses with a warning.

- Standardized filament fracture handling: all breaks trigger an Unload and Pause.

## Ver 0.1.0340

- Commented out the final mms_buffer.halfway() in Charge for a smoother swap.

- Changed default Delivery parameters: speed_drive: 80, accel_drive: 40.

- Updated RFID development plans; paused due to Elegoo's lack of RFID filament.

## Ver 0.1.0336 ~ 0.1.0339

- Merged upstream Klipper Master updates (commit 2e58023).

- Smoothed the SWAP process:

  - Eject: If purge is enabled, move to the Tray point before and after cutting.

  - Purge: Disabled pressure_pulse_cleaning by default and removed the final mms_buffer.halfway().

  - Brush: Disabled Peck by default.

  - Swap: Moved the final mms_buffer.halfway() to occur before mms_brush.

- Fixed a bug where Pause/Resume failed after a failed Resume: A timer now resets the is_paused flag to True 1 second after a failed resume, allowing subsequent resume attempts.

- Fixed a Z-axis homing issue after multiple Pause/Resume cycles by modifying the Z-axis recovery strategy in Swap.

## Ver 0.1.0335

- Removed printing restrictions for many MMS commands to allow use during prints, and added aliases to prevent accidental UI triggers:

  - Buffer commands: MMS_BUFFER_FILL/CLEAR/HALFWAY/ACTIVATE/DEACTIVATE/MEASURE.

  - Delivery commands: MMS_SELECT->MMS_SELECT_U, MMS_LOAD->MMS_LOAD_U, MMS_POP->MMS_POP_U, MMS_PREPARE->MMS_PREPARE_U. MMS_UNLOAD/MOVE/UNSELECT unchanged.

  - Swap commands: MMS_EJECT, MMS_CHARGE, MMS_PURGE, MMS_TRAY, MMS_TRAY_EJECT, MMS_CUT, MMS_BRUSH, MMS_BRUSH_WIPE, MMS_BRUSH_PECK.

- Drastically reduced console log output. Key logs (SLOT Pin changes, warnings/errors, user macros, SWAP mapping, MMS status) remain, while detailed process logs are directed to the mms.log file only.

## Ver 0.1.0333

- Attempted to modify Klipper's pins.py to support multiple endstops per homing move, but the implementation failed at the C-level and was shelved.

- Refactored SLOT Pins related classes into core/slot_pin.py. Updated references in other modules.

- Created SLOTGateInvert but did not instantiate it due to the shelved homing feature.

## Ver 0.1.0332

- Adjusted function names and updated calls in other modules.

- Adjusted member registration in the extend module.

- Improved control over the is_paused flag in mms_resume to prevent "Print is not paused, resume aborted" issues after Pause/Resume failures.

- Optimized the Eject workflow to prioritize and sort loading slots, improving safety for multi-slot scenarios.

- Deleted legacy mms_eject process code.

## Ver 0.1.0331

- Added MMS version output to the last_breath log.

- Modified toolhead_adapter to restore toolhead snapshot and heat to target temperature without waiting (wait=False).

- Made Delivery stepper state waits more strict: even async tasks will block and wait (5s timeout) if the stepper is not idle.

- Fixed a bug where the mms_pause flag was incorrectly cleared after a failed Resume retry in the Pause/Resume flow.

## Ver 0.1.0330

- Moved the MFRC522 RFID driver from Klipper's extras directory into the mms/hardware module, removing its generic config loading functionality and integrating it specifically for MMS use.

## Ver 0.1.0326 ~ 0.1.0328

- Fixed a bug in mms_eject exception handling related to incorrect slot_num reference.

- Enhanced Pause/Resume logic to prevent duplicate resume submissions and associated issues (like "Move out of range" errors) by better controlling the is_paused state flag.

## Ver 0.1.0323 ~ 0.1.0325

- Optimized the Charge retry logic, reducing nested retries from 9 to 6 attempts.

- Introduced ChargeFailedError to trigger LED blinking on charge failures.

- Updated several test machines to this version.

## Ver 0.1.0321 ~ 0.1.0322

- Fixed a bug where resuming and deactivating an LED blinking effect could cause a Reactor error and Klipper shutdown due to changes in timer state management.

- Revised the Eject workflow to improve safety, especially for handling multiple loaded slots and determining when to cut filament. Introduced EjectFailedError to trigger LED blinking on failure.

## Ver 0.1.0320

- Integrated Klipper's Motion Queuing system, which uses separate threads for each stepper motor managed by a global module.

- Resolved the root cause of Extruder/Drive movement blockage, which was a call to toolhead.flush_step_generation() within ManualStepper.do_set_position().

- Modified SLOT LED initialization to light up sequentially with a delay.

## Ver 0.1.0316 ~ 0.1.0319

- Continued merging upstream Klipper updates, focusing on the new Motion Queuing system which replaces manual step generation and flushing.

- Removed DripMoveDispatch and related logic.

- Fixed MMSStepper.reset_position() to correctly update the ManualStepper's internal position, resolving blockages when Extruder and Drive movements occurred simultaneously.

## Ver 0.1.0313 ~ 0.1.0315

- Adapted MMS to upstream Klipper updates (commit 'b4c7cf4'), specifically changes in the Klippy connect/ready callbacks that no longer allow reactor.pause().

- Modified Autoload startup delay and SLOT LED initialization to avoid using pause in callbacks.

- Updated PeriodicTask in Task.py to handle changes in the Reactor's timer management.

## Ver 0.1.0312

- Further reduced console logging, keeping full logs only in mms.log.

- Merged and adapted to upstream Klipper updates from 2025-08-12 to 2025-11-19 (including MCU, Toolhead, motion queuing, and Reactor changes). No firmware re-flash required.

## Ver 0.1.0311

- Reverted the mms_charge._standard_charge() logic in version 0.1.0310 back to depending on the Outlet (PA5) sensor due to reliability issues with the new method.

## Ver 0.1.0310

- Reduced console log noise; detailed logs are now only written to the mms.log file.

- Modified mms_charge and mms_buffer.halfway() logic to rely less on the Outlet (PA5) sensor by using the new four-phase movement capabilities.

- Updated mms_buffer.halfway() to use buffer_runout sensor for positioning.

## Ver 0.1.0306

- Updated Observer logic to monitor VirtualSD.must_pause_work for more accurate print pause state detection, addressing timing issues with PrintStats.state.

## Ver 0.1.0304 ~ 0.1.0305

- Extracted Pause/Resume logic into dedicated MMS_PAUSE and MMS_RESUME modules to centralize management and resolve recurring issues.

- MMS_RESUME intercepts and manages the standard resume command execution.

## Ver 0.1.0303

- Enhanced temperature control: toolhead_adapter now saves and restores target temperatures during Pause/Resume.

- Addressed potential Pause recursion issues and internal data persistence problems within Adapters modules after Klipper service restarts.

- Commented out the idle_timeout callback manager to resolve related issues.

## Ver 0.1.0302

- Added user Macro hooks (*_CUSTOM_BEFORE/AFTER) for Swap, Eject, Charge, Purge, and Brush modules, allowing user script injection into the standard workflow.

## Ver 0.1.0301

- Decoupled motion direction from sensor trigger/release states in Delivery/Stepper, enabling all four movement combinations (forward/backward to trigger/release).

- Changed mms_buffer.clear() to use unload_to_buffer_runout() instead of the old fill() + retract method.

- Simplified measure_stroke() implementation.

- Removed remaining Selector Swing code.

## Ver 0.1.0300

- Enhanced the Toolhead snapshot feature to store/restore target_temp and use non-blocking heating on resume.

- Removed toolhead_adapter.release_pressure calls during Purge for smoother operation.

- Added a pre-check for stepper_enable.is_motor_enable() in Stepper.enable() to prevent potential toolhead stuttering.

## Ver 0.1.0298 ~ 0.1.0299

- Added manual truncate_snapshot call on Swap failure to prevent the toolhead from moving back to the print point incorrectly.

- Added wait steps for Selector/Drive steppers in apply_nozzle_priming() to prevent skipping during asynchronous tasks.

## Ver 0.1.0295 ~ 0.1.0297

- Tested the Pressure Pulse Cleaning feature.

- Removed the safety logic that limited apply_nozzle_priming distance based on buffer spring stroke; now uses the configured value directly.

- Fixed safety check logic when slot_from is None during a Swap.

- Fixed an LED blinking effect deactivation bug by switching to use stepper events (mms:stepper:running/idle) for triggering.

- Removed all Stepper Soft Stop related code and configurations.

## Ver 0.1.0291 ~ 0.1.0294

- Added Pressure Pulse Cleaning functionality to the Purge module for nozzle maintenance and material flushing, to be used before cutting or cold pulling.

- Removed the ParkPoint feature and its related code and configurations.

## Ver 0.1.0290

- Implemented a new, type-safe configuration parsing system with a class inheritance structure for all *.cfg files.

- Standardized and enhanced English comments in all MMS Swap module configuration files for better clarity and documentation.

## Ver 0.1.0286

- Streamlined the Swap process flow, cleaned up code, and added comprehensive logging and comments.

- Conducted testing on the new Swap modules (Cut, Eject, Charge, Purge, Brush, Swap).

## Ver 0.1.0285

- Refactored the Swap module, restructuring it into separate, focused components: swap.py, charge.py, cut.py, eject.py, purge.py, brush.py, and utils.py.

- Each module now provides business-specific checks, state management, and exception handling.

## Ver 0.1.0283

- Clarified and structured the Swap process flow, defining enable/disable conditions for each step: eject, cut, mms_delivery.unload, charge, purge, and brush.

## Ver 0.1.0282

- Refactored Toolhead movement logic in Adapters. Abstracted XYZ-axis movement scheduling from gcode_move_adapter into toolhead_adapter.

- Added Toolhead snapshot feature to save/restore XYZ position, extruder temperature, and fan speed.

- Added Fan cooldown functionality accessible across mms_clean, mms_purge, and mms_swap modules.

## Ver 0.1.0281

- Completed iterations on Clean module functions: fan_cooldown, brush wipe/peck, and retraction_compensation.

- Added (non-operational) logic for wipe_cold (cold wiping).

## Ver 0.1.0280

- Separated Purge from Clean module. Purge now handles internal cleaning (hot/cold purging, pressure equalization). Clean handles external cleaning (brush wiping, tray knock-off).

- Purge module introduces adaptive purge length calculation based on material type and color differences using a matrix mapping system.

- Implemented different purge strategies for setups with/without a cutter and with/without a purge tray.

- Enhanced brush_wipe with adaptive calculations, material-specific temperatures, droplet-drip steps, cold wiping, and multi-directional/pecking motions.

- Implemented knock_off_blob action sequence for blob ejection from a purge tray.

## Ver 0.1.0271 ~ 0.1.0273

- Changed exception handling for Endstop Homing errors. Previously, MMS Stepper code caught all Exceptions, silently suppressing underlying CommandErrors from HomingMove. Now these CommandErrors are allowed to propagate.

- Introduced a DeliveryCommandError to wrap the original CommandError and stack trace for better debugging.

- If MMS catches a CommandError, it now directly calls Printer.emergency_stop for a cleaner shutdown process.

## Ver 0.1.0270

- Enhanced status reporting for external UIs. Iterated on the MMS module's get_status(eventtime) function to integrate data via Klipper/Moonraker hooks.

- Consolidated and structured status returns from mms.slot_meta, mms_slot, mms_stepper, and mms_buffer objects for external consumption (primarily by KlipperScreen).

## Ver 0.1.0263

- Fixed critical TTC (Toolhead Collision) issues that could occur if:

  - A Buffer Feed/Retract operation caused a re-grip in the Delivery module.

  - A Swap operation (like T*) started before a Buffer Feed/Retract operation finished.

- Strategy: Simplified Buffer Feed to use basic Drive movement without re-gripping or filament fracture detection. Added wait steps (up to 15s) for previous Selector/Drive operations to finish before starting a Swap.

## Ver 0.1.0261 ~ 0.1.0262

- Enhanced MMS_Buffer to support mapping across multiple ViViD units, binding SLOTs and Buffers.

- Added the MMS_BUFFER_MEASURE SLOT=n command for manual stroke calibration.

- Removed legacy Dripload process code and old buffer module files.

- Fixed a bug where move_backward() in Buffer stroke measurement did not stop as expected when the Runout Pin was triggered.

- Fixed a bug where purge would not abort and pause the print if an intermediate feed step failed, improving error handling in Clean/Swap workflows.

## Ver 0.1.0260

- Added an automatic Buffer Spring Stroke measurement and calibration feature that runs on the first T* operation after each boot.

- Renamed DriploadButton/Dripbutton to buffer_runout/runout to reflect its new role in the MMS_Buffer system. All related code and configuration references updated.

- Completely disabled the legacy Dripload functionality (both in config and code).

- Note: Firmware Update Required. The C-level SoftStop() function was removed, making ManualMove operations un-interruptable again via MMS_STOP.

## Ver 0.1.0256~0.1.0259

- Conducted performance analysis on the Buffer Feed/Retract system, showing highly stable operation times.

- Removed the cmd_MMS_SWAP related commands.

- Decoupled Clean-Purge logic from MMS_Buffer. Purge now has its own independent filament feeding logic, using a simpler synchronous, segmented approach based on the Buffer Spring stroke to prevent filament grinding.

- Enhanced Purge finish with a small, fast retract and a buffer state check to ensure safety.

## Ver 0.1.0253~0.1.0255

- Fully migrated all Dripload functionality to the new MMS_Buffer system.

- Updated handling for absolute_extrude setting and improved sensor state checks to prevent incorrect volume calculations.

- Improved Feed/Retract logic during printing, significantly reducing filament grinding incidents.

- Added speed-matching logic during Swap Purge phases to prevent over-compression.

- Updated configuration files: changed sensor logic to non-inverted and adjusted default purge distances.

## Ver 0.1.0252

- Added new commands: MMS_BUFFER_ACTIVATE and MMS_BUFFER_DEACTIVATE.

- Fixed a major issue where long-distance retracts caused abnormal high-speed retract attempts. Switched to using the extruder's actual position instead of the virtual gcode_position for calculations.

- Added a step to force-load to the Outlet sensor at the end of the Charge operation to calibrate the MMS_Buffer's maximum volume.

## Ver 0.1.0251

- Fixed a bug in mms_swap.relax_spring() related to inverted sensor logic. The function is now deprecated as the continuous retract strategy makes spring relaxation unnecessary.

- Continued refinement of the Neo Buffer's data acquisition, switching to use real-time extruder position data.

## Ver 0.1.0250

- Redesigned the Buffer Spring Feed system, moving away from a complex spring compression model towards a simpler capacity-based "Neo Buffer" concept.

- Replaced the producer-consumer queue model with a more straightforward Reactor/PeriodicTask mechanism to improve stability and reduce system load.

## Ver 0.1.0245

- Officially implemented the Buffer Spring Feed feature.

- Introduces a monitoring strategy to calculate spring compression ratio in real-time using extruder motion data.

- Dynamically calculates required feed distance, speed, and acceleration based on live extrusion flow rate to maintain optimal buffer state.

- Implements a producer-consumer model with a task queue (FeedQueue) for managing feed requests.

- Includes safety mechanisms: checks Outlet state before feeding, limits individual feed distance, and clears queue on sensor triggers.

## Ver 0.1.0242

- Continued investigation and testing regarding Toolhead stuttering, focusing on soft_break() and underlying stepper communication.

- Enhanced Dripload startup checks: now verifies Outlet sensor state and Extruder action type (Extrude/Retract).

- Began design and development of a dynamic Buffer Spring Feed system based on real-time extrusion flow rate.

## Ver 0.1.0241

- Fixed an issue where MMS Toolhead and Extruder speeds were incorrectly scaled by the print speed/extrusion factor (M220).

- Investigated and implemented fixes for occasional Toolhead stuttering observed during printing.

- Removed the restriction preventing Dripload activation during Purge operations.

## Ver 0.1.0240

- Improved multi-stage unload logic in Swap to address occasional first-stage failure.

- Changed the relax_spring() method to unload until the Drip (PA4) sensor is released, instead of using extrusion.

- Adjusted the initial Extruder retract logic for the first unload stage.

## Ver 0.1.0234

- Renamed service.py to task.py.

- Optimized Delivery logic to prevent repeated execution of safety retract under specific conditions.

- Intercepted the RESUME command at the code level to ensure cached MMS commands are executed upon resuming, fixing issues caused by third-party config overrides.

## Ver 0.1.0233

- Fixed a potential issue with an incorrect print_time in Stepper enable(), resolving motor humming/failure to move.

- Added handling for underlying MCU query exceptions in soft_stop().

- Consolidated idle-wait functions in the Delivery module.

- Removed the restriction that prevented Dripload from triggering during Pause.

## Ver 0.1.0232

- Adjusted config loading order; MMS_SLOT/MMS_EXTEND now uses delayed initialization.

- Improved the Dripload distance calibration functionality.

- Fixed a bug where Dripload deactivated after Eject, causing unload failure.

- Disabled the Selector Swing function by default.

## Ver 0.1.0231

- Major Dripload Improvement: Introduced dynamic distance calculation and calibration, using statistical methods to optimize stopping distance and reduce damage from abrupt stops.

- Fixed a bug in the Adapter that occurred during config save & restart.

## Ver 0.1.0230

- Continued code structure adjustments, separating GlobalKlippy and management modules.

- Changed the initialization methods for LED and Service-related modules.

## Ver 0.1.0225

- Restructured the MMS module directory, organizing it into core, hardware, motion, etc.

## Ver 0.1.0224

- Cleaned up duplicate code in Adapters, standardized the use of lookup_object(), and purified business classes.

## Ver 0.1.0223

- Implemented remaining core adapters (ForceMoveAdapter, StepperEnableAdapter, VirtualSDCardAdapter, etc.).

- Fully integrated the previous Assist and Observer mechanisms into the Adapter system.

## Ver 0.1.0222

- Fixed an issue in Adapters where the global Printer object was not updated correctly after a restart.

- Implemented adapters like ExtruderAdapter, GCodeMoveAdapter, and NeopixelAdapter.

- Decoupled business classes from Klipper configuration dependencies.

## Ver 0.1.0221

- Added the crucial PrinterAdapter for managing printer objects and events.

- Implemented GCodeAdapter and FanAdapter, updating related communication code.

- Cleaned up and removed obsolete utility functions.

## Ver 0.1.0220

- Introduced the Adapters architecture to abstract interactions with Klipper modules.

- Implemented ToolheadAdapter and migrated related logic from other modules.

## Ver 0.1.0214

- Refactored the Observer callback mechanism and added CallbackManager.

- Added a new Exceptions module to centralize and manage exceptions from various modules, enhancing exception linkage handling.

- Added the MMS_LED_EFFECT_TRUNCATE command to clear all LED effects at once.

- Added exception handling callbacks for Slots.

- Added LED effect reminders for failed Slot operations.

- Added a safety retract function to Delivery, default 50mm.

- Adjusted Autoload logic to accommodate the safety retract.

## Ver 0.1.0212

- Fixed variable naming issues in the Delivery module.

- Refactored the Observer module, splitting it into multiple independent observers and improving the state judgment mechanism.

## Ver 0.1.0211

- Added parameter validation to mms.get_slot().

- Optimized the Delivery process by adding wait steps to prevent operational conflicts.

- Refactored and optimized the Selector Swing logic.

## Ver 0.1.0210

- Added Selector Swing Test feature, enabling Selector to swing during the unload phase (for testing only).

- Supports dynamic adjustment of swing parameters via the MMS_D_TEST command.

## Ver 0.1.0206

- Changed the default Inlet configuration in mms-slot.cfg from mechanical switch to photoelectric switch.

- Enhanced LED error handling; a configuration mismatch no longer prevents startup and alerts the user.

## Ver 0.1.0205

- Standardized and cleaned up error log output formats.

- Fixed bugs in filament fracture detection; added automatic mms_load after resume to fill the gap between Gate and Outlet.

## Ver 0.1.0203

- Added support for filament fracture detection in Advanced Dripload.

- Made Semi-Autoload asynchronous and interruptible with MMS_STOP.

- Temporarily disables fracture detection during mms_pop to prevent false triggers.

- Improved Observer.is_printing() logic.

- Differentiated fracture handling between Swap/Charge and Dripload scenarios.

## Ver 0.1.0202

- Implemented Filament Fracture Detection based on the Inlet sensor during HomingMove loading/unloading.

## Ver 0.1.0201

- Replaced stepper.pause() with completion.wait() in MoveDispatch.

- Changed Button trigger/release callbacks to a FIFO deque.

- Enhanced SLOT callback control using context managers.

## Ver 0.1.0200

- Implemented Advanced Dripload, triggered by PA4 and stopped by PA5.

- Fixed a bug in semi_load_to_gate parameters that prevented Semi-Autoload from working correctly.

## Ver 0.1.0196

- Encapsulated motor control logic in MMSStepper and ManualMoveDispatch classes for asynchronous, interruptible movement termination.

- Note: High-frequency termination may cause stepcompress errors; use is limited to low-frequency scenarios like Advanced Dripload.

## Ver 0.1.0195

- Implemented Stepper Soft Stop in C (command_stepper_soft_stop()), requiring a firmware re-flash for ViViD.

- Changed fan control logic in Swap/Clean/Cut modules; only mms_clean controls fans during Purge.

## Ver 0.1.0192

- Added monitoring for the Dripload Pin in MMS_STATUS and updated log messages.

## Ver 0.1.0191

- Restored Autoload interruption and RFID Detect interruption features based on the new motor stopping logic.

- Restored the MMS_STOP/MMS999 command functionality.

## Ver 0.1.0190

- Implemented Trsync Break Homing, enabling emergency stops for HomingMove operations (e.g., for filament fracture detection).

## Ver 0.1.0184

- Fixed a bug where a failed Swap Pause could cause recursive PAUSE calls and incorrect task state flags.

- Fixed a compilation error in sensor_ads1220.c after merging from upstream.

## Ver 0.1.0183

- Adjusted configuration: changed single Dripload distance from 15mm to 10mm.

- Began prototyping filament fracture detection.

## Ver 0.1.0182

- Minor refactoring of the Delivery module, reducing redundant code in selection/delivery functions.

## Ver 0.1.0181

- Fixed a bug in Selector where the focus wasn't updated after selection, which could cause get_current_slot to misjudge.

## Ver 0.1.0180

- Merged upstream Klipper Master updates (commit 3ef760c).

## Ver 0.1.0173

- Introduced Extend functionality, enabling support for multiple ViViD units for multi-color printing.

- Changed the RESUME macro override method for better reliability.

## Ver 0.1.0160

- Refactored the Stepper module, consolidating motion scheduling logic. Merged MOVE/MOVE_TO/MOVE_UNTIL into a DRIP_MOVE type.

- Added automatic MMS_EJECT at the end of a print.

- Changed the gear_ratio for the drive stepper in the sample configuration.

## Ver 0.1.0155

- Minor refactoring of EJECT, CHARGE, and SWAP logic.

- Fixed a bug where MMS_CHARGE did not stop after a load failure.

- Added automatic MMS_EJECT at the end of a print (complete/error/cancelled).

## Ver 0.1.0152

- Changed Drive scheduling in _deliver_distance() from move() to manual_move().

- Minor refactoring of EJECT and SWAP logic.

## Ver 0.1.0150

- Added Semi-Autoload feature with the MMS_SEMI_AUTOLOAD command.

- Added the ability to pass movement distance as an external parameter in Delivery.

## Ver 0.1.0143

- Changed Gear (now Drive) Stepper to use Klipper's native manual_home for sensor-triggered moves, reducing delay and filament crushing.

- Changed the Outlet sensor pin to buffer:PA5 (closer to the physical limit).

- Decoupled the Dripload monitor pin from the Outlet, using a dedicated pin (e.g., !buffer:PA4).

- Updated naming conventions: Buffer sensor -> Gate, Gear stepper -> Drive stepper.

## Ver 0.1.0111

- Fixed a bug where the Entry sensor trigger did not stop the MMS_LOAD command.

- Fixed an initialization error where mms_selector was referenced before being ready in get_status.

## Ver 0.1.0110

- Major refactoring of the SLOT module, decoupling Pin state management, LED control, Autoload, and RFID logic.

- Added optional support for an Entry Sensor; loading logic prioritizes Entry over Outlet if configured.

- Added Pause/Resume Swap recovery: resumes will retry the last swap command if printing was paused during a swap.

- Removed the restriction on Deliver operations (e.g., MMS_LOAD, MMS_MOVE) while printing is paused.
