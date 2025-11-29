# Exceptions and Signals for MMS
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging


class MMSException(Exception):
    """Base exception for MMS operations."""

    def __init__(self, msg=None, mms_slot=None):
        super().__init__(msg)
        self.mms_slot = mms_slot
        # Action as soon as exception is raised
        self._execute_raise_action()

    def _execute_raise_action(self):
        """Execute slot action when exception is raised."""
        # hanler_name = "handle_mms_exception_raised"

        # if self.mms_slot and hasattr(self.mms_slot, hanler_name):
        if self.mms_slot:
            try:
                self.mms_slot.handle_mms_exception_raised(self)
            except Exception as e:
                logging.error(f"MMS: '{self}' execute raise action error: {e}")


class DeliveryFailedError(MMSException):
    """
    Raised when Selector/Inlet/Gate/Outlet... sensor is
    not triggered/released after full HomingMove.
    """


class DeliveryPreconditionError(MMSException):
    """
    Raised when preconditions for
    delivery operations are not met.
    """


class DeliveryReadyError(MMSException):
    """
    Raised when SLOT Inlet is not
    triggered before delivery.
    """


class EjectFailedError(MMSException):
    """
    Raised when SLOT Eject is failed.
    """


class ChargeFailedError(MMSException):
    """
    Raised when SLOT Charge is failed.
    """


class PurgeFailedError(MMSException):
    """
    Raised when SLOT Charge is failed.
    """


# ==== Excetion Signals ====
class SwapFailedSignal(Exception):
    """Raised when swap operation fails."""


class DeliveryTerminateSignal(Exception):
    """Terminate signal."""
