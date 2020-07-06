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
from octowire.utils.serial_utils import detect_octowire
from prompt_toolkit import prompt


class Baudrates(AModule):
    def __init__(self, owf_config):
        super(Baudrates, self).__init__(owf_config)
        self.meta.update({
            'name': 'UART baudrate detection',
            'version': '1.0.0',
            'description': 'Perform UART baudrate detection',
            'author': 'Jordan Ovrè / Ghecko <jovre@immunit.ch>, Paul Duncan / Eresse <pduncan@immunit.ch>'
        })
        self.options = {
            "uart_interface": {"Value": "", "Required": True, "Type": "int",
                               "Description": "The Octowire UART interface (0=UART0 or 1=UART1)", "Default": 0},
            "trigger": {"Value": "", "Required": True, "Type": "bool",
                        "Description": "When true, send a newline if the Octowire\ndoes not receive"
                                       "anything from the target",
                        "Default": False}
        }
        self.vowels = ["a", "A", "e", "E", "i", "I", "o", "O", "u", "U"]
        self.whitespace = [" ", "\t", "\r", "\n"]
        self.punctation = [".", ",", ":", ";", "?", "!"]
        self.control = [b'\x0e', b'\x0f', b'\xe0', b'\xfe', b'\xc0', b'\x0d', b'\x0a']
        self.baudrates = [9600, 19200, 38400, 57600, 115200]

    @staticmethod
    def wait_bytes(uart_instance):
        timeout = 1
        timeout_start = time.time()

        while time.time() < timeout_start + timeout:
            in_waiting = uart_instance.in_waiting()
            if in_waiting > 0:
                return True
        return False

    def gen_char_list(self):
        """
        Generate human readable character list.
        :return: character list
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

    def change_baudrate(self, uart_instance, baudrate):
        """
        This function changes the baudrate for the target device.
        :param uart_instance: Octowire UART instance.
        :param baudrate: Baudrate dictionary (decimal and hexadecimal value)
        :return: bool
        """
        self.logger.handle(f'Switching to baudrate {baudrate}...', self.logger.INFO)
        try:
            # Empty serial_instance buffer
            uart_instance.serial_instance.read(uart_instance.serial_instance.in_waiting)
            # Configure UART baudrate
            uart_instance.configure(baudrate=baudrate)
            # Empty UART in_waiting buffer
            uart_instance.receive(uart_instance.in_waiting())
            return True
        except (ValueError, Exception) as err:
            self.logger.handle(err, self.logger.ERROR)
            return False

    def trigger_device(self, uart_instance):
        """
        Send a carriage return to trigger the target device
        in case nothing is received
        """
        self.logger.handle("Triggering the device", self.logger.INFO)
        uart_instance.transmit(b'\x0D\x0A')
        time.sleep(0.2)

    def uart_pt_miniterm(self, uart_instance):
        uart_instance.passthrough()
        self.owf_serial.close()
        config = load_config()
        if self.advanced_options["detect_octowire"]["Value"]:
            octowire_port = detect_octowire(verbose=False)
            config['OCTOWIRE']['port'] = octowire_port
        else:
            octowire_port = self.advanced_options["detect_octowire"]["Value"]
            octowire_baudrate = self.advanced_options["baudrate"]["Value"]
            config['OCTOWIRE']['port'] = octowire_port
            config['OCTOWIRE']['baudrate'] = octowire_baudrate
        miniterm(None, config)
        self.logger.handle("Please press the Octowire User button to exit the UART "
                           "passthrough mode", self.logger.USER_INTERACT)

    def baudrate_detect(self):
        """
        The main function. Change the baudrate
        and check if bytes received on the RX pin are valid characters.
        25 valid characters are required to identify the correct baudrate value.
        """
        count = 0
        whitespace = 0
        punctuation = 0
        vowels = 0
        threshold = 20
        valid_characters = self.gen_char_list()

        uart_interface = self.options["uart_interface"]["Value"]
        trigger = self.options["trigger"]["Value"]

        # Set and configure UART interface
        uart_instance = UART(serial_instance=self.owf_serial, interface_id=uart_interface)

        self.logger.handle("Starting baurate detection, turn on your serial device now", self.logger.HEADER)
        self.logger.handle("Press Ctrl+C to cancel", self.logger.HEADER)

        for baudrate in self.baudrates:
            loop = 0
            if self.change_baudrate(uart_instance=uart_instance, baudrate=baudrate):
                # Dynamic printing
                progress = self.logger.progress('Reading bytes')
                while True:
                    if self.wait_bytes(uart_instance=uart_instance):
                        tmp = uart_instance.receive(1)
                        try:
                            tmp.decode()
                            progress.status(tmp.decode())
                        except UnicodeDecodeError:
                            tmp2 = tmp
                            progress.status('0x{}'.format(codecs.encode(tmp2, 'hex').decode()))
                        try:
                            byte = tmp.decode('utf-8')
                        except UnicodeDecodeError:
                            byte = tmp
                        if byte in valid_characters:
                            if byte in self.whitespace:
                                whitespace += 1
                            elif byte in self.punctation:
                                punctuation += 1
                            elif byte in self.vowels:
                                vowels += 1
                            count += 1
                        else:
                            # Invalid character, reset counter and switch baudrate
                            whitespace = 0
                            punctuation = 0
                            vowels = 0
                            count = 0
                            progress.stop()
                            self.logger.handle("{} does not appear to be a valid baudrate setting...".format(baudrate),
                                               self.logger.WARNING)
                            break
                        if count >= threshold and whitespace > 0 and punctuation >= 0 and vowels > 0:
                            progress.stop()
                            self.logger.handle("Valid baudrate found: {}".format(baudrate), self.logger.RESULT)
                            resp = prompt('Would you like to open a miniterm session ? N/y: ')
                            if resp.upper() == 'Y':
                                self.uart_pt_miniterm(uart_instance=uart_instance)
                                break
                            break
                    elif trigger and loop < 3:
                        loop += 1
                        self.trigger_device(uart_instance)
                        continue
                    else:
                        progress.stop()
                        self.logger.handle("No data received using the following baudrate "
                                           "value: {}...".format(baudrate), self.logger.WARNING)
                        break

    def run(self):
        """
        Main function.
        Try to detect a valid UART baudrate.
        :return:
        """
        # If detect_octowire is True then detect and connect to the Octowire hardware. Else, connect to the Octowire
        # using the parameters that were configured. This sets the self.owf_serial variable if the hardware is found.
        self.connect()
        if not self.owf_serial:
            return
        try:
            self.baudrate_detect()
        except (Exception, ValueError) as err:
            self.logger.handle(err, self.logger.ERROR)
