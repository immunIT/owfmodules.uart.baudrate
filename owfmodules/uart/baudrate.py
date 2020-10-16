# -*- coding: utf-8 -*-

# Octowire Framework
# Copyright (c) ImmunIT - Jordan Ovrè / Paul Duncan
# License: Apache 2.0
# Paul Duncan / Eresse <pduncan@immunit.ch>
# Jordan Ovrè / Ghecko <jovre@immunit.ch>

import codecs
import time

from octowire_framework.module.AModule import AModule
from octowire_framework.core.commands.miniterm import miniterm
from octowire_framework.core.config import load_config
from octowire.uart import UART
from octowire.gpio import GPIO
from octowire.utils.serial_utils import detect_octowire
from prompt_toolkit import prompt


class Baudrate(AModule):
    def __init__(self, owf_config):
        super(Baudrate, self).__init__(owf_config)
        self.meta.update({
            'name': 'UART baudrate detection',
            'version': '2.0.0',
            'description': 'Perform UART baudrate detection',
            'author': 'Jordan Ovrè / Ghecko <jovre@immunit.ch>, Paul Duncan / Eresse <pduncan@immunit.ch>'
        })
        self.options = {
            "uart_interface": {"Value": "", "Required": True, "Type": "int",
                               "Description": "The Octowire UART interface (0=UART0 or 1=UART1)", "Default": 0},
            "mode": {"Value": "", "Required": True, "Type": "text",
                     "Description": "Method used to perform the baudrate detection.\nIn the 'incremental' mode, the "
                                    "baudrate is incremented by the 'baudrate_increment' advanced option starting from "
                                    "'baudrate_min' and ending with the 'baudrate_max' advanced options \nIn the 'list'"
                                    " mode, the baudrate value defined in the 'baudrate_list' advanced option will be "
                                    "tested. Acceptable values: 'list' & 'incremental'.",
                     "Default": "incremental"},
            "reset_pin": {"Value": "", "Required": False, "Type": "int",
                          "Description": "GPIO used as the Reset line. If defined, the module will try to reset the "
                                         "target using the defined GPIO. See the 'reset_dir' advanced option to "
                                         "defined the direction.",
                          "Default": ""},
            "trigger": {"Value": "", "Required": True, "Type": "bool",
                        "Description": "When true, send the characters defined by the 'trigger_char' advanced options "
                                       "if the Octowire does not receive anything from the target",
                        "Default": False}
        }
        self.advanced_options.update({
            "reset_dir": {"Value": "", "Required": True, "Type": "text",
                          "Description": "The direction of the reset line to reset the target. "
                                         "Acceptable values: 'low' & 'high'",
                          "Default": "low"},
            "reset_hold": {"Value": "", "Required": True, "Type": "float",
                           "Description": "'Reset_pin' hold time required to perform a reset (in seconds)",
                           "Default": 0.1},
            "reset_delay": {"Value": "", "Required": True, "Type": "float",
                            "Description": "The time to wait after a reset attempt.",
                            "Default": 0.5},
            "baudrate_min": {"Value": "", "Required": True, "Type": "int",
                             "Description": "The minimal baudrate value to test. (Incremental mode only)",
                             "Default": 300},
            "baudrate_max": {"Value": "", "Required": True, "Type": "int",
                             "Description": "The maximum baudrate value to test. (Incremental mode only)",
                             "Default": 115200},
            "baudrate_increment": {"Value": "", "Required": True, "Type": "int",
                                   "Description": "The baudrate incremental value. (Incremental mode only)",
                                   "Default": 300},
            "baudrate_list": {"Value": "", "Required": True, "Type": "text",
                              "Description": "The baudrate values to test (comma separated). (List mode only)",
                              "Default": "9600,19200,38400,57600,115200"},
            "trigger_char": {"Value": "", "Required": True, "Type": "hextobytes",
                             "Description": "The character(s) to send when the 'trigger' options is set to True. "
                                            "Format: raw hex (no leading '0x')",
                             "Default": "0D0A"},
        })
        self.vowels = ["a", "A", "e", "E", "i", "I", "o", "O", "u", "U"]
        self.whitespace = [" ", "\t", "\r", "\n"]
        self.punctation = [".", ",", ":", ";", "?", "!"]
        self.control = [b'\x0e', b'\x0f', b'\xe0', b'\xfe', b'\xc0', b'\x0d', b'\x0a']
        self.baudrates = [9600, 19200, 38400, 57600, 115200]
        self.uart_instance = None
        self.reset_line = None
        self.valid_characters = None

    def check_options(self):
        """
        Check the user's defined options.
        :return: Bool.
        """
        # If reset_pin is set and reset_dir invalid
        if self.options["reset_pin"]["Value"] != "":
            if self.advanced_options["reset_dir"]["Value"].upper() not in ["LOW", "HIGH"]:
                self.logger.handle("Invalid reset direction.", self.logger.ERROR)
                return False
            if self.options["reset_pin"]["Value"] not in range(0, 15):
                self.logger.handle("Invalid reset pin.", self.logger.ERROR)
                return False
        # Check the mode
        if self.options["mode"]["Value"].upper() not in ["INCREMENTAL", "LIST"]:
            self.logger.handle("Invalid mode option. Please use 'incremental' or 'list'", self.logger.ERROR)
            return False
        # Check the list if the selected mode is 'list'
        if self.options["mode"]["Value"].upper() == "LIST":
            try:
                baud_list = [b.strip() for b in self.advanced_options["baudrate_list"]["Value"].split(",")]
                if not baud_list:
                    self.logger.handle("Empty or invalid baudrate list.", self.logger.ERROR)
                    return False
            except:
                self.logger.handle("Invalid baudrate list", self.logger.ERROR)
        return True

    def wait_bytes(self):
        """
        Wait until receiving a bytes (for 1 seconds) from the target.
        :return: Bool.
        """
        timeout = 1
        timeout_start = time.time()

        while time.time() < timeout_start + timeout:
            in_waiting = self.uart_instance.in_waiting()
            if in_waiting > 0:
                return True
        return False

    def gen_char_list(self):
        """
        Generate human readable character list.
        :return: character list.
        :rtype: List.
        """
        c = ' '
        valid_characters = []
        while c <= '~':
            valid_characters.append(c)
            c = chr(ord(c) + 1)

        for c in self.whitespace:
            if c not in valid_characters:
                valid_characters.append(c)

        for c in self.control:
            if c not in valid_characters:
                valid_characters.append(c)
        return valid_characters

    def change_baudrate(self, baudrate):
        """
        This function changes the baudrate for the target device.
        :param baudrate: Baudrate dictionary (decimal and hexadecimal value)
        :return: Bool.
        """
        self.logger.handle(f'Switching to baudrate {baudrate}...', self.logger.INFO)
        try:
            # Empty serial_instance buffer
            self.uart_instance.serial_instance.read(self.uart_instance.serial_instance.in_waiting)
            # Configure UART baudrate
            self.uart_instance.configure(baudrate=baudrate)
            # Empty UART in_waiting buffer
            self.uart_instance.receive(self.uart_instance.in_waiting())
            return True
        except (ValueError, Exception) as err:
            self.logger.handle(err, self.logger.ERROR)
            return False

    def trigger_device(self):
        """
        Send a character(s) defined by the "trigger_char" advanced option.
        This method is called whn no byte was receive during the baudrate detection.
        :return: Nothing.
        """
        self.logger.handle("Triggering the device", self.logger.INFO)
        self.uart_instance.transmit(self.advanced_options["trigger_char"]["Value"])
        time.sleep(0.2)

    def uart_pt_miniterm(self):
        """
        Open a miniterm session, with the Octowire in the UART passthrough mode
        if a valid baudrate value is found and the user select 'yes' when asked.
        :return: Nothing.
        """
        self.uart_instance.passthrough()
        self.owf_serial.close()
        config = load_config()
        if self.config["OCTOWIRE"]["detect"]:
            octowire_port = detect_octowire(verbose=False)
            config['OCTOWIRE']['port'] = octowire_port
        miniterm(None, config)
        self.logger.handle("Please press the Octowire User button to exit the UART "
                           "passthrough mode", self.logger.USER_INTERACT)

    def process_baudrate(self, baudrate):
        """
        The main function. Change the baudrate
        and check if bytes received on the RX pin are valid characters.
        20 valid characters are required to identify the correct baudrate value.
        :return: Bool.
        """
        count = 0
        whitespace = 0
        punctuation = 0
        vowels = 0
        threshold = 20

        loop = 0
        # Dynamic printing
        progress = self.logger.progress('Reading bytes')
        while True:
            if self.wait_bytes():
                tmp = self.uart_instance.receive(1)
                # Print character read dynamically
                try:
                    tmp.decode()
                    progress.status(tmp.decode())
                except UnicodeDecodeError:
                    tmp2 = tmp
                    progress.status('0x{}'.format(codecs.encode(tmp2, 'hex').decode()))
                # Try to decode the received byte
                try:
                    byte = tmp.decode('utf-8')
                except UnicodeDecodeError:
                    byte = tmp
                # Check if it is a valid character.
                if byte in self.valid_characters:
                    if byte in self.whitespace:
                        whitespace += 1
                    elif byte in self.punctation:
                        punctuation += 1
                    elif byte in self.vowels:
                        vowels += 1
                    count += 1
                else:
                    # Invalid character, quit the loop and try with the next baudrate value
                    progress.stop()
                    self.logger.handle("{} does not appear to be a valid baudrate setting...".format(baudrate),
                                       self.logger.WARNING)
                    return False
                if count >= threshold and whitespace > 0 and punctuation >= 0 and vowels > 0:
                    progress.stop()
                    self.logger.handle("Valid baudrate found: {}".format(baudrate), self.logger.RESULT)
                    resp = prompt('Would you like to open a miniterm session ? N/y: ')
                    if resp.upper() == 'Y':
                        self.uart_pt_miniterm()
                    return True
            elif self.options["trigger"]["Value"] and loop < 3:
                loop += 1
                self.trigger_device()
                continue
            else:
                progress.stop()
                self.logger.handle("No data received using the following baudrate "
                                   "value: {}...".format(baudrate), self.logger.WARNING)
                return False

    def reset_target(self):
        """
        If the reset_pin option is set, reset the target depending of the reset direction.
        :return: Nothing
        """
        if self.reset_line is not None:
            self.logger.handle("Attempt to reset the target..", self.logger.INFO)
            if self.advanced_options["reset_dir"]["Value"].upper() == "LOW":
                self.reset_line.status = 0
                time.sleep(self.advanced_options["reset_hold"]["Value"])
                self.reset_line.status = 1
            else:
                self.reset_line.status = 1
                time.sleep(self.advanced_options["reset_hold"]["Value"])
                self.reset_line.status = 0
            time.sleep(self.advanced_options["reset_delay"]["Value"])

    def init(self):
        """
        Configure the UART and the reset interface (if defined).
        Create the list of valid characters.
        :return:
        """
        # Set and configure UART interface
        self.uart_instance = UART(serial_instance=self.owf_serial, interface_id=self.options["uart_interface"]["Value"])

        # Unsure reset_line is set to None before initialized it if needed
        self.reset_line = None
        # Configure the reset line if defined
        if self.options["reset_pin"]["Value"] != "":
            self.reset_line = GPIO(serial_instance=self.owf_serial, gpio_pin=self.options["reset_pin"]["Value"])
            self.reset_line.direction = GPIO.OUTPUT
            if self.advanced_options["reset_dir"]["Value"].upper() == "LOW":
                self.reset_line.status = 1
            else:
                self.reset_line.status = 0

        # Set the list of valid characters
        self.valid_characters = self.gen_char_list()

    def incremental_mode(self):
        """
        Check for valid baudrate using the incremental mode.
        :return: Nothing.
        """
        for baudrate in range(self.advanced_options["baudrate_min"]["Value"],
                              self.advanced_options["baudrate_max"]["Value"],
                              self.advanced_options["baudrate_increment"]["Value"]):
            if self.change_baudrate(baudrate=baudrate):
                self.reset_target()
                if self.process_baudrate(baudrate=baudrate):
                    # Stop the for loop if valid baudrate is found
                    break

    def list_mode(self):
        """
        Check for valid baudrate using the list mode.
        :return: Nothing.
        """
        for baudrate in [int(b.strip()) for b in self.advanced_options["baudrate_list"]["Value"].split(",")]:
            if self.change_baudrate(baudrate=baudrate):
                self.reset_target()
                if self.process_baudrate(baudrate=baudrate):
                    # Stop the for loop if valid baudrate is found
                    break

    def run(self):
        """
        Main function.
        Try to detect a valid UART baudrate.
        :return: Nothing.
        """
        # If detect_octowire is True then detect and connect to the Octowire hardware. Else, connect to the Octowire
        # using the parameters that were configured. This sets the self.owf_serial variable if the hardware is found.
        self.connect()
        if not self.owf_serial:
            return
        try:
            if self.check_options():
                self.init()
                self.logger.handle("Starting baurate detection, turn on your serial device now", self.logger.HEADER)
                self.logger.handle("Press Ctrl+C to cancel", self.logger.HEADER)
                if self.options["mode"]["Value"].upper() == "INCREMENTAL":
                    self.incremental_mode()
                elif self.options["mode"]["Value"].upper() == "LIST":
                    self.list_mode()
            else:
                return
        except (Exception, ValueError) as err:
            self.logger.handle(err, self.logger.ERROR)
