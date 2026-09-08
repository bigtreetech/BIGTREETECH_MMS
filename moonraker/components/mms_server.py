# Bigtreetech MMS Software
# Moonraker support for a file-preprocessor that injects MMS metadata into gcode files
#
# Adapted from Happy Hare MMU Software
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
from __future__ import annotations
import json
import logging, os, sys, re, time, asyncio
import runpy, argparse, shutil, traceback, tempfile, filecmp
from typing import (
    TYPE_CHECKING,
    List,
    Dict,
    Any,
    Optional,
    Union,
    cast
)

if TYPE_CHECKING:
    from .spoolman import SpoolManager, DB_NAMESPACE, ACTIVE_SPOOL_KEY
    from ..common import WebRequest
    from ..common import RequestType
    from ..confighelper import ConfigHelper
    from .http_client import HttpClient, HttpResponse
    from .database import MoonrakerDatabase
    from .announcements import Announcements
    from .klippy_apis import KlippyAPI as APIComp
    from .history import History
    from tornado.websocket import WebSocketClientConnection

MMS_NAME_FIELD   = 'printer_name'
MMS_GATE_FIELD   = 'mms_gate_map'
MIN_SM_VER       = (0, 18, 1)

DB_NAMESPACE     = "moonraker"
ACTIVE_SPOOL_KEY = "spoolman.spool_id"

class MmsServer:
    def __init__(self, config: ConfigHelper):
        self.config = config
        self.server = config.get_server()
        logging.info("MMS server: __init__")
        self.printer_info = self.server.get_host_info()
        self.spoolman = None
        if config.has_section("spoolman"): # Avoid exception if spoolman not configured
            self.spoolman: SpoolManager = self.server.load_component(config, "spoolman", None)
        self.spoolman: SpoolManager = self.server.lookup_component("spoolman", None)
        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")
        self.http_client: HttpClient = self.server.lookup_component("http_client")
        self.database: MoonrakerDatabase = self.server.lookup_component("database")
        logging.info(f"MMS server: components looked up, database: {self.database}")

        # Full cache of spool_ids and location + key attributes (printer, gate, attr_dict))
        self.spool_location = {}

        self.nb_gates = None             # Set during initialization to the size of the MMS or 1 if standalone
        self.cache_lock = asyncio.Lock() # Lock to serialize a async calls

        # Spoolman filament info retrieval functionality and update reporting
        if self.spoolman:
            self.server.register_remote_method("spoolman_refresh", self.refresh_cache)
            self.server.register_remote_method("spoolman_get_filaments", self.get_filaments) # "get" mode
            self.server.register_remote_method("spoolman_push_gate_map", self.push_gate_map) # "push" mode
            self.server.register_remote_method("spoolman_pull_gate_map", self.pull_gate_map) # "pull" mode
            self.server.register_remote_method("spoolman_clear_spools_for_printer", self.clear_spools_for_printer)
            self.server.register_remote_method("spoolman_set_spool_gate", self.set_spool_gate)
            self.server.register_remote_method("spoolman_unset_spool_gate", self.unset_spool_gate)
            self.server.register_remote_method("spoolman_get_spool_info", self.display_spool_info)
            self.server.register_remote_method("spoolman_display_spool_location", self.display_spool_location)

        # Moonraker lane data push for slicer integration
        self.server.register_remote_method("moonraker_push_lane_data", self.push_lane_data)
        self.server.register_remote_method("moonraker_pull_lane_data", self.pull_lane_data)
        self.server.register_remote_method("moonraker_cleanup_lane_data", self.cleanup_lane_data)

        # Replace file_manager/metadata with this file
        self.setup_placeholder_processor(config)

        # Options
        self.update_location = self.config.getboolean("update_spoolman_location", True)

    async def _get_spoolman_version(self) -> tuple[int, int, int] | None:
        response = await self.http_client.get(url=f'{self.spoolman.spoolman_url}/v1/info')
        if response.status_code == 404:
            logging.info(f"'{self.spoolman.spoolman_url}/v1/info' not found")
            return False
        elif response.has_error():
            err_msg = self.spoolman._get_response_error(response)
            logging.error(f"Attempt to get info from spoolman failed: {err_msg}")
            return False
        else:
            logging.info("info field in spoolman retrieved")
            return tuple([int(n) for n in response.json()['version'].split('.')])

    async def component_init(self) -> None:
        logging.info("MMS server: component_init")
        if self.spoolman is None:
            logging.warning("Spoolman not available. Spoolman remote methods not available")

        # Get current printer hostname
        self.printer_hostname = self.printer_info["hostname"]
        self.spoolman_has_extras = False
        if self.spoolman:
            asyncio.create_task(self._init_spoolman(retry=3)) # Spoolman may start up after us so retry a few times
        else:
            logging.info("MMS server: Skipping Spoolman init as component not found")

    async def _init_spoolman(self, retry=1) -> bool:
        '''
        Return True if connected, False if not. Set's self.spoolman_has_extras is
        '''
        async with self.cache_lock:
            for _ in range(retry):
                self.spoolman_version = await self._get_spoolman_version()
                if self.spoolman_version:
                    logging.info("Contacted Spoolman")
                    break
                logging.warning(f"Spoolman not available. {'Retrying in 2 seconds...' if retry > 1 else ''}")
                await asyncio.sleep(2)

            extras = False
            if self.spoolman_version and self.spoolman_version >= MIN_SM_VER:
                # Make sure db has required extra fields
                extras = True
                fields = await self._get_extra_fields("spool")
                if MMS_NAME_FIELD not in fields:
                    extras = extras and await self._add_extra_field("spool", field_name="Printer Name", field_key=MMS_NAME_FIELD, field_type="text", default_value="")
                if MMS_GATE_FIELD not in fields:
                    extras = extras and await self._add_extra_field("spool", field_name="MMS Gate", field_key=MMS_GATE_FIELD, field_type="integer", default_value=-1)

                # Create cache of spool location from Spoolman db for effeciency
                if extras:
                    await self._build_spool_location_cache(silent=True)
                self.spoolman_has_extras = extras

            elif self.spoolman_version:
                logging.error(f"Could not initialize Spoolman db for MMS. Spoolman db version too old (found {self.spoolman_version} < {MIN_SM_VER})")
            else:
                logging.error("Could not connect to Spoolman db. Perhaps it is not initialized yet? Will try again on next request")
                return False
        return True

    async def _check_init_spoolman(self, silent=False) -> bool:
        if not self.spoolman_has_extras:
            db_awake = await self._init_spoolman()
            if not silent:
                if not db_awake:
                    await self._log_n_send("Couldn't connect to Spoolman. Maybe not configured/running yet (check moonraker.log).\nUse MMS_SPOOLMAN REFRESH=1 to force retry")
                elif not self.spoolman_has_extras:
                    await self._log_n_send("Incompatible Spoolman version for this feature. Check moonraker.log")
        return self.spoolman_has_extras

    async def _log_n_send(self, msg, error=False, prompt=False, silent=False):
        '''
        logs and sends msg to the klipper console
        '''
        if error:
            logging.error(msg)
        else:
            logging.info(msg)
        if not silent:
            if self._mms_backend_enabled():
                error_flag = "ERROR=1" if error else ""
                msg = msg.replace("\n", "\\n") # Get through klipper filtering
                await self.klippy_apis.run_gcode(f"MMS_LOG MSG='{msg}' {error_flag}")
            else:
                for msg in msg.split("\n"):
                    await self.klippy_apis.run_gcode(f"M118 {msg}")
                if error :
                    await self.klippy_apis.pause_print()

    async def _init_mms_backend(self):
        '''
        Initialize MMS backend and check if enabled
        '''
        self.mms_backend_present = 'mms' in await self.klippy_apis.get_object_list()
        if self.mms_backend_present:
            self.mms_backend_config = await self.klippy_apis.query_objects({"mms": None})
            # Assume enabled if present for MMS
            self.mms_enabled = True 
        else:
            self.mms_enabled = False
        logging.info(f"MMS backend present: {self.mms_backend_present}")
        logging.info(f"MMS backend enabled: {self.mms_enabled}")
        return True

    def _mms_backend_enabled(self):
        if not hasattr(self, 'mms_backend_present'):
            return False
        return self.mms_backend_present and self.mms_enabled

    async def _initialize_mms(self):
        '''
        Initialize mms gate map if not already done
        '''
        if not hasattr(self, 'mms_backend_present'):
            await self._init_mms_backend()
            if self._mms_backend_enabled():
                if self.config.has_option("num_gates"):
                    logging.warning("The 'num_gates' option in the moonraker [mms_server] section is ignored when an MMS backend is present and enabled.")
                # Get slot count from Klipper object list if possible, defaulting to 4
                objects = await self.klippy_apis.get_object_list()
                slots = [obj for obj in objects if obj.startswith('mms_slot ')]
                self.nb_gates = len(slots) if slots else 4
            else:
                self.nb_gates = self.config.getint("num_gates", 1)
            logging.info(f"MMS num_gates: {self.nb_gates}")
        return True

    async def _get_extra_fields(self, entity_type) -> bool:
        '''
        Helper to gets all extra fields for the entity type
        '''
        response = await self.http_client.get(url=f'{self.spoolman.spoolman_url}/v1/field/{entity_type}')
        if response.status_code == 404:
            logging.info(f"'{self.spoolman.spoolman_url}/v1/field/{entity_type}' not found")
            return False
        elif response.has_error():
            err_msg = self.spoolman._get_response_error(response)
            logging.error(f"Attempt to get extra fields failed: {err_msg}")
            return False
        else:
            logging.info(f"Extra fields for {entity_type} found")
            return [r['key'] for r in response.json()]

    async def _add_extra_field(self, entity_type, field_key, field_name, field_type, default_value) -> bool:
        '''
        Helper to add a new field to the extra field of the Spoolman db
        '''
        response = await self.http_client.post(
            url=f'{self.spoolman.spoolman_url}/v1/field/{entity_type}/{field_key}',
            body={"name" : field_name, "field_type" : field_type, "default_value" : json.dumps(default_value)}
        )
        if response.status_code == 404:
            logging.info(f"'{self.spoolman.spoolman_url}/v1/field/spool/{field_key}' not found")
            return False
        elif response.has_error():
            err_msg = self.spoolman._get_response_error(response)
            logging.error(f"Attempt add field {field_name} failed: {err_msg}")
            return False
        logging.info(f"Field {field_name} added to Spoolman db for entity type {entity_type}")
        logging.info("  -fields: %s", response.json())
        return True

    async def _fetch_spool_info(self, spool_id) -> dict | None:
        '''
        Retrieve an individual spool_info record
        '''
        response = await self.spoolman.http_client.request(
            method="GET",
            url=f'{self.spoolman.spoolman_url}/v1/spool/{spool_id}',
            body=None)
        if response.status_code == 404:
            logging.error(f"'{self.spoolman.spoolman_url}/v1/spool/{spool_id}' not found")
            return None
        elif response.has_error():
            err_msg = self.spoolman._get_response_error(response)
            logging.info(f"Attempt to fetch spool info failed: {err_msg}")
            return None
        spool_info = response.json()
        return spool_info

    def _get_filament_attr(self, spool_info) -> dict:
        spool_id = spool_info["id"]
        filament = spool_info["filament"]
        name = filament.get('name', '')
        material = filament.get('material', '')
        color_hex = filament.get('color_hex', '').strip('#')[:8].lower() # Remove problematic First # character if present
        temp = filament.get('settings_extruder_temp', '')
        bed_temp = filament.get('settings_bed_temp', '')
        vendor = filament.get('vendor', {}).get('name', '')
        filament_id = filament.get('id', '')
        return {'spool_id': spool_id, 'material': material, 'color': color_hex, 'name': name, 'temp': temp, 'bed_temp': bed_temp, 'vendor': vendor, 'filament_id': filament_id}

    async def _build_spool_location_cache(self, fix=False, silent=False) -> bool:
        '''
        Helper to get all spools and gates assigned to printers from Spoolman db and cache them
        '''
        logging.info("Building spool location cache from Spoolman db")
        try:
            self.spool_location.clear()
            # Fetch all spools
            errors = ""
            assignments = {}
            sids_to_fix = []
            reponse = await self.http_client.get(url=f'{self.spoolman.spoolman_url}/v1/spool')
            for spool_info in reponse.json():
                spool_id = spool_info['id']
                printer_name = json.loads(spool_info['extra'].get(MMS_NAME_FIELD, "\"\"")).strip('"')
                mms_gate = int(spool_info['extra'].get(MMS_GATE_FIELD, -1))
                filament_attr = self._get_filament_attr(spool_info)
                self.spool_location[spool_id] = (printer_name, mms_gate, filament_attr)

                if printer_name and mms_gate >= 0:
                    if printer_name not in assignments:
                        assignments[printer_name] = {}
                    if mms_gate not in assignments[printer_name]:
                        assignments[printer_name][mms_gate] = []
                    assignments[printer_name][mms_gate].append(spool_id)

                # Highlight errors
                if printer_name and mms_gate < 0:
                    errors += f"\n  - Spool {spool_id} has printer {printer_name} but no mms_gate assigned"
                    sids_to_fix.append(spool_id)
                if mms_gate >= 0 and not printer_name:
                    errors += f"\n  - Spool {spool_id} has mms_gate {mms_gate} but no printer assigned"
                    sids_to_fix.append(spool_id)

            for p, gates in assignments.items():
                for g, spool_list in gates.items():
                    if len(spool_list) > 1:
                        errors += f"\n  - Printer {p} @ gate {g} has multiple spool ids: {spool_list}"
                        sids_to_fix.extend(spool_list[1:])
        except Exception as e:
            await self._log_n_send(f"Failed to retrieve spools from spoolman: {str(e)}", error=True, silent=silent)
            return False

        if errors:
            if fix:
                errors += "\nWill attempt to fix..."
            await self._log_n_send(f"Warning - Inconsistencies found in Spoolman db:{errors}", silent=silent)

        if fix:
            tasks = {sid: self._unset_spool_gate(sid, silent=silent) for sid in sids_to_fix}
            results = await asyncio.gather(*tasks.values())

            # Log results and update cache
            for sid, result in zip(tasks.keys(), results):
                if result:
                    old_printer, old_gate, filament_attr = self.spool_location.get(sid, ('', -1, {}))
                    self.spool_location[sid] = ('', -1, filament_attr)
                    await self._log_n_send(f"Spool {sid} unassigned from printer {old_printer} and gate {old_gate}", silent=silent)
        return True

    def _find_first_spool_id(self, target_printer, target_gate):
        return next((spoolid
                for spoolid, (printer, gate, _) in self.spool_location.items()
                if (target_printer is None or printer == target_printer) and gate == target_gate
            ), -1)

    def _find_all_spool_ids(self, target_printer, target_gate):
        return [
            spoolid
            for spoolid, (printer, gate, _) in self.spool_location.items()
            if (target_printer is None or printer == target_printer) and (target_gate is None or gate == target_gate)
        ]

    async def _set_spool_gate(self, spool_id, printer, gate, silent=False) -> bool:
        if not await self._check_init_spoolman(): return

        if not silent:
            logging.info(f"Setting spool {spool_id} for printer {printer} @ gate {gate}")
        data = {'extra': {MMS_NAME_FIELD: json.dumps(f"{printer}"), MMS_GATE_FIELD: json.dumps(gate)}}
        if self.update_location:
            data['location'] = f"{printer} @ MMS Gate:{gate}"
        response = await self.http_client.request(
            method="PATCH",
            url=f"{self.spoolman.spoolman_url}/v1/spool/{spool_id}",
            body=json.dumps(data)
        )
        if response.status_code == 404:
            logging.error(f"'{self.spoolman.spoolman_url}/v1/spool/{spool_id}' not found")
            await self._log_n_send(f"SpoolId {spool_id} not found", error=True, silent=False)
            return False
        elif response.has_error():
            err_msg = self.spoolman._get_response_error(response)
            logging.error(f"Attempt to set spool failed: {err_msg}")
            await self._log_n_send(f"Failed to set spool {spool_id} for printer {printer}. Look at moonraker.log for more details.", error=True, silent=False)
            return False
        return True

    async def _unset_spool_gate(self, spool_id, silent=False) -> bool:
        if not await self._check_init_spoolman(): return

        if not silent:
            logging.info(f"Unsetting gate map on spool id {spool_id}")
        data = {'extra': {MMS_NAME_FIELD: json.dumps(""), MMS_GATE_FIELD: json.dumps(-1)}}
        if self.update_location:
            data['location'] = ""
        response = await self.http_client.request(
            method="PATCH",
            url=f"{self.spoolman.spoolman_url}/v1/spool/{spool_id}",
            body=json.dumps(data)
        )
        if response.status_code == 404:
            logging.error(f"'{self.spoolman.spoolman_url}/v1/spool/{spool_id}' not found")
            await self._log_n_send(f"SpoolId {spool_id} not found", error=True, silent=False)
            return False
        elif response.has_error():
            err_msg = self.spoolman._get_response_error(response)
            logging.error(f"Attempt to unset spool failed: {err_msg}")
            await self._log_n_send(f"Failed to unset spool {spool_id}. Look at moonraker.log for more details", error=True, silent=False)
            return False
        return True

    async def _send_gate_map_update(self, gate_ids, replace=False, silent=False) -> bool:
        if self._mms_backend_enabled():
            gate_dict = {
                gate: (
                    {'spool_id': -1} if spool_id < 0 else
                    self.spool_location.get(spool_id)[2].copy()
                    if self.spool_location.get(spool_id)
                    else logging.error(f"Spool id {spool_id} requested but not found in spoolman")
                )
                for gate, spool_id in gate_ids
            }
            try:
                # Assuming MMS might not have a direct equivalent to MMU_GATE_MAP, we might just log or pass
                await self._log_n_send(f"MMS Spoolman Gate Map Update: {gate_dict}", silent=silent)
            except Exception as e:
                await self._log_n_send(f"Exception running MMS Gate Map update: {str(e)}", error=True, silent=silent)
                return False
        return True

    async def refresh_cache(self, fix=False, silent=False) -> bool:
        if not await self._check_init_spoolman(): return
        async with self.cache_lock:
            await self._initialize_mms()
            return await self._build_spool_location_cache(fix=fix, silent=silent)

    async def get_filaments(self, gate_ids, silent=False) -> bool:
        async with self.cache_lock:
            return await self._send_gate_map_update(gate_ids, silent=silent)

    async def push_gate_map(self, gate_ids=None, silent=False) -> bool:
        if not await self._check_init_spoolman(): return
        async with self.cache_lock:
            await self._initialize_mms()

            if not gate_ids:
                logging.error("Gate spool id mapping not provided or empty")
                return False

            updates = {}
            for gate, spool_id in gate_ids:
                old_sids = self._find_all_spool_ids(self.printer_hostname, gate)
                for old_sid in old_sids:
                    updates[old_sid] = -1

            for gate, spool_id in gate_ids:
                if spool_id > 0:
                    updates[spool_id] = gate

            if len(gate_ids) == self.nb_gates:
                for spool_id, (p_name, gate, _) in self.spool_location.items():
                    if p_name == self.printer_hostname and not any(s == spool_id for _, s in gate_ids):
                        updates[spool_id] = -1

            tasks = {
                sid: (
                    self._unset_spool_gate(sid, silent=silent),
                    None
                ) if updates[sid] < 0 else (
                    self._set_spool_gate(sid, self.printer_hostname, updates[sid], silent=silent),
                    updates[sid]
                )
                for sid in updates.keys()
            }
            results = await asyncio.gather(*[task for task,_ in tasks.values()])

            for sid, result in zip(tasks.keys(), results):
                if result:
                    old_printer, old_gate, filament_attr = self.spool_location.get(sid, ('', -1, {}))
                    gate = tasks[sid][1]
                    if updates[sid] < 0:
                        self.spool_location[sid] = ('', -1, filament_attr)
                        self.server.send_event("spoolman:unset_spool_gate", {"spool_id": sid, "printer": old_printer, "gate": old_gate})
                        await self._log_n_send(f"Spool {sid} unassigned from printer {old_printer} and gate {old_gate} in Spoolman db", silent=silent)
                    else:
                        self.spool_location[sid] = (self.printer_hostname, gate, filament_attr)
                        self.server.send_event("spoolman:set_spool_gate", {"spool_id": sid, "printer": self.printer_hostname, "gate": gate})
                        await self._log_n_send(f"Spool {sid} assigned to printer {self.printer_hostname} @ gate {gate} in Spoolman db", silent=silent)

            return await self._send_gate_map_update(gate_ids, silent=silent)

    async def pull_gate_map(self, silent=False) -> bool:
        if not await self._check_init_spoolman(): return
        async with self.cache_lock:
            await self._initialize_mms()

            gate_ids = [(gate, self._find_first_spool_id(self.printer_hostname, gate)) for gate in range(self.nb_gates)]
            return await self._send_gate_map_update(gate_ids, replace=True, silent=silent)

    async def clear_spools_for_printer(self, printer=None, sync=False, silent=False) -> bool:
        if not await self._check_init_spoolman(): return
        async with self.cache_lock:
            await self._initialize_mms()

            printer_name = printer or self.printer_hostname
            if not silent:
                logging.info(f"Clearing gate map for printer: {printer_name}")

            old_sids = self._find_all_spool_ids(printer_name, None)
            tasks = {sid: self._unset_spool_gate(sid, silent=silent) for sid in old_sids}
            results = await asyncio.gather(*tasks.values())

            updated_gate_ids = {}
            for sid, result in zip(tasks.keys(), results):
                if result:
                    old_printer, old_gate, filament_attr = self.spool_location.get(sid, ('', -1, {}))
                    if old_printer == self.printer_hostname and 0 <= old_gate < self.nb_gates and not updated_gate_ids.get(old_gate):
                        updated_gate_ids[old_gate] = -1
                    self.spool_location[sid] = ('', -1, filament_attr)
                    self.server.send_event("spoolman:unset_spool_gate", {"spool_id": sid, "printer": old_printer, "gate": old_gate})
                    await self._log_n_send(f"Spool {sid} unassigned from printer {old_printer} and gate {old_gate}", silent=silent)

            self.server.send_event("spoolman:clear_spool_gates", {"printer": printer_name})
            if sync and updated_gate_ids:
                gate_ids = [(gate, spool_id) for gate, spool_id in updated_gate_ids.items()]
                return await self._send_gate_map_update(gate_ids, replace=True, silent=silent)
            return True

    async def set_spool_gate(self, spool_id=None, gate=None, sync=False, silent=False) -> bool:
        if not await self._check_init_spoolman(): return
        async with self.cache_lock:
            await self._initialize_mms()

            if gate is not None and gate < 0:
                await self._log_n_send("Trying to set spool {spool_id} for printer {self.printer_hostname} but gate {gate} is invalid.", error=True, silent=silent)
                return False
            if gate is not None and gate > self.nb_gates - 1:
                await self._log_n_send(f"Trying to set spool {spool_id} for printer {self.printer_hostname} @ gate {gate} but only {self.nb_gates} gates are available. Please check the spoolman or moonraker [spoolman] setup.", error=True, silent=silent)
                return False
            if gate is None:
                if self.nb_gates:
                    await self._log_n_send(f"Trying to set spool {spool_id} for printer {self.printer_hostname} but printer has an MMS with {self.nb_gates} gates. Please check the spoolman or moonraker [spoolman] setup.", error=True, silent=silent)
                    return False
                gate = 0

            if not silent:
                logging.info(f"Attempting to set gate {gate} for printer {self.printer_hostname}")

            old_sids = self._find_all_spool_ids(self.printer_hostname, gate)
            tasks = {
                sid: (self._unset_spool_gate(sid, silent=silent), None)
                for sid in old_sids if sid != spool_id
            }
            tasks[spool_id] = (self._set_spool_gate(spool_id, self.printer_hostname, gate, silent=silent), gate)
            results = await asyncio.gather(*[task for task,_ in tasks.values()])

            updated_gate_ids = {}
            for sid, result in zip(tasks.keys(), results):
                if result:
                    old_printer, old_gate, filament_attr = self.spool_location.get(sid, ('', -1, {}))
                    gate = tasks[sid][1]
                    if sid in old_sids and sid != spool_id:
                        if old_printer == self.printer_hostname and 0 <= old_gate < self.nb_gates and not updated_gate_ids.get(old_gate):
                            updated_gate_ids[old_gate] = -1
                        self.spool_location[sid] = ('', -1, filament_attr)
                        self.server.send_event("spoolman:unset_spool_gate", {"spool_id": sid, "printer": old_printer, "gate": old_gate})
                        await self._log_n_send(f"Spool {sid} unassigned from printer {old_printer} and gate {old_gate} in Spoolman db", silent=silent)
                    else:
                        if 0 <= gate < self.nb_gates:
                            if old_printer == self.printer_hostname and 0 <= old_gate < self.nb_gates and not updated_gate_ids.get(old_gate):
                                updated_gate_ids[old_gate] = -1
                            updated_gate_ids[gate] = sid
                        self.spool_location[sid] = (self.printer_hostname, gate, filament_attr)
                        self.server.send_event("spoolman:set_spool_gate", {"spool_id": sid, "printer": self.printer_hostname, "gate": gate})
                        await self._log_n_send(f"Spool {sid} assigned to printer {self.printer_hostname} @ gate {gate} in Spoolman db", silent=silent)

            if sync and updated_gate_ids:
                gate_ids = [(gate, spool_id) for gate, spool_id in updated_gate_ids.items()]
                return await self._send_gate_map_update(gate_ids, replace=True, silent=silent)
            return True

    async def unset_spool_gate(self, spool_id=None, gate=None, sync=False, silent=False) -> bool:
        if not await self._check_init_spoolman(): return
        async with self.cache_lock:
            await self._initialize_mms()

            if spool_id is None and gate is None:
                await self._log_n_send("Trying to unset spool but no spool_id or gate provided", error=True, silent=silent)
                return False
            if spool_id is not None and gate is not None:
                await self._log_n_send(f"Trying to unset spool but both spool_id {spool_id} and gate {gate} provided. Only one or the other expected", error=True, silent=silent)
                return False
            if spool_id is not None:
                if not self.spool_location.get(spool_id, ('', -1, {})):
                    await self._log_n_send(f"Trying to unset spool {spool_id} but not found in cache. Perhaps try refreshing cache", error=True, silent=silent)
                    return False

            sids = self._find_all_spool_ids(self.printer_hostname, gate) if gate is not None else [spool_id]
            tasks = {sid: self._unset_spool_gate(sid, silent=silent) for sid in sids}
            results = await asyncio.gather(*tasks.values())

            updated_gate_ids = {}
            for sid, result in zip(tasks.keys(), results):
                if result:
                    old_printer, old_gate, filament_attr = self.spool_location.get(sid, ('', -1, {}))
                    if old_printer == self.printer_hostname and 0 <= old_gate < self.nb_gates and not updated_gate_ids.get(old_gate):
                        updated_gate_ids[old_gate] = -1
                    self.spool_location[sid] = ('', -1, filament_attr)
                    self.server.send_event("spoolman:unset_spool_gate", {"spool_id": sid, "old_printer": self.printer_hostname, "old_gate": gate})
                    await self._log_n_send(f"Spool {sid} unassigned from printer {old_printer} and gate {old_gate} in Spoolman db", silent=silent)

            if sync and updated_gate_ids:
                gate_ids = [(gate, spool_id) for gate, spool_id in updated_gate_ids.items()]
                return await self._send_gate_map_update(gate_ids, replace=True, silent=silent)
            return True

    async def display_spool_info(self, spool_id: int | None = None):
        async with self.cache_lock:
            active = "Spool"

            if not spool_id:
                logging.info("Fetching active spool")
                spool_id = await self.spoolman.database.get_item(DB_NAMESPACE, ACTIVE_SPOOL_KEY, None)
                active = "Active spool"

            if not spool_id:
                msg = "No active spool set and no spool id supplied"
                await self._log_n_send(msg, error=True)
                return False

            spool_info = await self._fetch_spool_info(spool_id)
            if not spool_info:
                msg = f"Spool id {spool_id} not found"
                await self._log_n_send(msg, error=True)
                return False

            material = spool_info.get('material', "n/a")
            used_weight = int(spool_info.get('used_weight', -1))
            f_used_weight = f"{used_weight} g" if used_weight >= 0 else "n/a"
            remaining_weight = int(spool_info.get('remaining_weight', -1))
            f_remaining_weight = f"{remaining_weight} g" if remaining_weight >= 0 else "n/a"
            msg = f"{active} is: {spool_info['filament']['name']} (id: {spool_info['id']})\n"
            msg += f"  - Material: {material}\n"
            msg += f"  - Used: {f_used_weight}\n"
            msg += f"  - Remaining: {f_remaining_weight}\n"

            spool = next((gate for sid, (printer, gate, _) in self.spool_location.items() if spool_id == sid and self.printer_hostname == printer), None)
            if spool is not None:
                msg += f"  - Gate: {spool}"
            else:
                msg += f"Spool id {spool_id} is not assigned to this printer!\n"
                msg += f"Run: MMS_SLOT_SPOOL SLOT=.. SPOOL_ID={spool_id} to add"
            await self._log_n_send(msg)
            return True

    async def display_spool_location(self, printer=None):
        if not await self._check_init_spoolman(): return
        async with self.cache_lock:
            await self._initialize_mms()
            printer_name = printer or self.printer_hostname
            filtered = sorted(((spool_id, gate) for spool_id, (printer, gate, _) in self.spool_location.items() if printer == printer_name), key=lambda x: x[1])
            if filtered:
                msg = f"Spoolman gate assignment for printer: {printer_name}\n"
                msg += "Gate | SpoolId\n"
                msg += "-----+--------\n"
                if self.nb_gates:
                    for mms_gate in range(self.nb_gates):
                        sids = [spool_id for (spool_id, gate) in filtered if gate == mms_gate]
                        sids_str = ",".join(map(str, sids))
                        warning = " Error: Can only have a single spool assigned" if len(sids) > 1 else ""
                        msg += f"{mms_gate:<5}| {sids_str}{warning}\n"
                else:
                    for spool_id, gate in filtered:
                        msg += f"{gate:<5}| {spool_id}\n"
            else:
                msg = f"No gates assigned for printer: {printer_name}"
            await self._log_n_send(msg)

    async def push_lane_data(self, lane_data):
        '''
        Pushes lane data to Moonraker database for slicer integration (OrcaSlicer)
        lane_data: dictionary of lane data objects mapped to 'lane{n}' keys
        '''
        try:
            if lane_data:
                await self.database.insert_batch("lane_data", lane_data)
                logging.info("MMS Lane Data successfully pushed to Moonraker")
        except Exception as e:
            logging.error(f"Error pushing lane data: {e}")

    async def pull_lane_data(self):
        '''
        Pulls lane data from Moonraker database and pushes it to Klipper via G-code
        '''
        try:
            db = self.server.lookup_component("database", None)
            if db is None:
                return {"error": "database component not found"}
                
            lane_items = await db.get_item("lane_data", None, {})
            
            if not lane_items and self.spoolman_has_extras:
                logging.info("MMS server: empty database, trying Spoolman fallback")
                await self._initialize_mms()
                lane_items = {}
                for gate in range(self.nb_gates):
                    spool_id = self._find_first_spool_id(self.printer_hostname, gate)
                    if spool_id > 0:
                        spool_info = self.spool_location.get(spool_id)
                        if spool_info:
                            attr = spool_info[2]
                            lane_items[f"lane{gate}"] = {
                                "vendor_name": attr.get('vendor'),
                                "name": attr.get('name'),
                                "material": attr.get('material'),
                                "color": attr.get('color'),
                                "bed_temp": attr.get('bed_temp'),
                                "nozzle_temp": attr.get('temp'),
                                "lane": str(gate),
                            }

            # Always reset all slots in Klipper first to ensure empty states are synced
            await self._initialize_mms()
            if self.nb_gates:
                logging.info(f"MMS server: resetting {self.nb_gates} slots in Klipper")
                reset_cmd = f"MMS_SLOT_MAP GATES={','.join(map(str, range(self.nb_gates)))} RESET=1 QUIET=1 SYNC=1"
                await self.klippy_apis.run_gcode(reset_cmd)

            if lane_items:
                logging.info(f"MMS server: pushing {len(lane_items)} lanes to Klipper")
                for lane_key, data in lane_items.items():
                    if not isinstance(data, dict): continue
                    slot = data.get('lane')
                    if slot is None: continue
                    
                    # Construct MMS_SLOT_MAP command
                    parts = [f"MMS_SLOT_MAP SLOT={slot} QUIET=1 SYNC=1"]
                    if data.get('material'): parts.append(f"MATERIAL='{data['material']}'")
                    if data.get('color'): parts.append(f"COLOR='{data['color']}'")
                    if data.get('vendor_name'): parts.append(f"VENDOR='{data['vendor_name']}'")
                    if data.get('name'): parts.append(f"NAME='{data['name']}'")
                    
                    # Handle temps (avoid 0.0 or None)
                    bt = data.get('bed_temp')
                    if bt and float(bt) > 0: parts.append(f"BED_TEMP={bt}")
                    nt = data.get('nozzle_temp')
                    if nt and float(nt) > 0: parts.append(f"NOZZLE_TEMP={nt}")
                    
                    cmd = " ".join(parts)
                    await self.klippy_apis.run_gcode(cmd)

            return lane_items or {}
        except Exception as e:
            logging.error(f"MMS server: Error in pull_lane_data: {e}")
            return {"error": str(e)}

    async def cleanup_lane_data(self, num_gates):
        '''
        Removes lane data for gates that no longer exist
        '''
        try:
            lane_items = await self.database.get_item("lane_data", None, {})
            keys_to_delete = []
            for lane_key, lane_value in lane_items.items():
                if isinstance(lane_value, dict):
                    lane_str = lane_value.get('lane', '')
                    try:
                        lane_num = int(lane_str)
                        if lane_num >= num_gates:
                            keys_to_delete.append(lane_key)
                    except (ValueError, TypeError):
                        continue

            if keys_to_delete:
                await self.database.delete_batch("lane_data", keys_to_delete)
                logging.info(f"Removed old lane data: {keys_to_delete}")

        except Exception as e:
            logging.error(f"Error cleaning up lane data: {e}")

    def setup_placeholder_processor(self, config):
        args = " -m" if config.getboolean("enable_file_preprocessor", True) else ""
        args += " -n" if config.getboolean("enable_toolchange_next_pos", True) else ""
        
        # Link the custom MMS preprocessor script to Moonraker's file_manager
        script_path = os.path.join(os.path.dirname(__file__), '../../scripts/mms_preprocessor.py')
        if os.path.exists(script_path):
             from moonraker.components.file_manager import file_manager
             file_manager.METADATA_SCRIPT = os.path.abspath(script_path) + args
             logging.info(f"MMS Preprocessor script linked: {file_manager.METADATA_SCRIPT}")
        else:
             logging.warning(f"MMS Preprocessor script not found at {script_path}")

def load_component(config):
    return MmsServer(config)
