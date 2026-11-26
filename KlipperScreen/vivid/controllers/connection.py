import logging


def get_mms_connection(screen):
    connection = getattr(screen, "_vivid_mms_connection", None)
    if connection is None:
        connection = screen._vivid_mms_connection = MMSConnection(screen)
    return connection


def subscribe_printer_objects(screen, updates):
    return get_mms_connection(screen).subscribe(updates)


class MMSConnection:
    def __init__(self, screen):
        self.screen = screen
        self._session = None
        self._printer = None
        self._transport = None
        self._available = None

    @property
    def connected(self):
        transport = getattr(self.screen, "_ws", None)
        return transport is not None and transport.connected and not transport.closing

    @property
    def available(self):
        transport = getattr(self.screen, "_ws", None)
        if self._printer is self.screen.printer and self._transport is transport:
            return self._available
        return None

    def send(self, method, params, callback=None):
        if not self.connected:
            return False
        transport, printer, session = self.screen._ws, self.screen.printer, self._session

        def receive(*args):
            # A late reply must not update a different printer or a restarted session.
            if (self.connected and self.screen._ws is transport
                    and self.screen.printer is printer and self._session is session):
                callback(*args)

        try:
            return transport.send_method(method, params, receive if callback else None)
        except Exception:
            logging.exception("ViViD could not send Moonraker request %s", method)
            return False

    def subscribe(self, updates):
        self._session = object()
        self._printer = self.screen.printer
        self._transport = self.screen._ws
        self._available = None
        if "mms" in (self._printer.data or {}):
            self._available = True
            return self._subscribe_mms(updates)

        # Let the host initialize immediately on versions that only query selected objects.
        sent = self.send("printer.objects.subscribe", updates)

        def discover(response, _method, _params):
            names = response.get("result", {}).get("objects")
            if not isinstance(names, list):
                logging.warning("ViViD could not discover printer objects")
                return
            self._available = "mms" in names
            if self._available:
                self._subscribe_mms(updates)
            else:
                self.screen.process_update("notify_status_update", {})

        if sent:
            self.send("printer.objects.list", {}, discover)
        return sent

    def _subscribe_mms(self, updates):
        # Moonraker replaces the subscription for this connection. Keep all host objects.
        objects = dict(updates["objects"], mms=None)
        params = dict(updates, objects=objects)
        before = dict(self.screen.printer.get_stat("mms"))

        def receive_status(response, _method, _params):
            snapshot = response.get("result", {}).get("status", {}).get("mms")
            if not isinstance(snapshot, dict):
                logging.warning("ViViD did not receive MMS status from the subscription")
                self.send("printer.objects.subscribe", updates)
                return
            printer = self.screen.printer
            # A status notification may arrive while the subscription is pending.
            changes = {key: value for key, value in printer.get_stat("mms").items()
                       if key not in before or value != before[key]}
            update = {"mms": {**snapshot, **changes}}
            printer.process_update(update)
            self.screen.process_update("notify_status_update", update)

        return self.send("printer.objects.subscribe", params, receive_status)

    def get_status(self):
        printer = self.screen.printer
        return printer.get_stat("mms") if self.connected and printer is not None else {}
