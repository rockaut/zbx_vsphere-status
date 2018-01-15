#!/usr/bin/env python3
"""
Zabbix vSphere Status
"""

__author__ = "Markus Fischbacher<fischbacher.markus@gmail.com>"
__version__ = "0.1.0"
__license__ = "GPL-3.0"


import argparse, os, sys, socket, http.client, time, datetime, urllib.parse, re
from xml.dom import minidom

class TargetConnection:
    port      = 443
    hostname  = None
    timeout   = 60
    checkcert = False

    __connection = None

    systeminfo = {}

    systemfields = [
        ("apiVersion", float),
        ("name", None),
        ("fullName", None),
        ("rootFolder", None),
        ("perfManager", None),
        ("sessionManager", None),
        ("licenseManager", None),
        ("licenseProductName", None),
        ("licenseProductVersion", None),
        ("propertyCollector", None),
        ("version", None),
        ("build", None),
        ("vendor", None),
        ("osType", None),
        ("apiType", None),
    ]

    def __init__(self, hostname):
        self.hostname = hostname

    def __del__(self):
        self.close()

    def connect(self):
        """Initialize connection to target system"""
        try:
            if self.checkcert:
                self.__connection = http.client.HTTPSConnection(self.hostname, self.port, timeout=self.timeout)
            else:
                try:
                    import ssl
                    self.__connection = http.client.HTTPSConnection(self.hostname, self.port, timeout=self.timeout, context=ssl._create_unverified_context())
                except expression as identifier:
                    raise
            
            self.__connection.connect()

            self.retrieve_systeminfo()
        except expression as identifier:
            raise
    
    def close(self):
        if self.__connection:
            self.__connection.close()

    def retrieve_systeminfo(self):
        """Retrieve basic data, which requires no login"""
        payload = self.xml_systeminfo
        reply_code, reply_msg, reply_headers, reply_data = self.query_target(payload)

        for entry, function in self.systemfields:
            element = self.get_pattern("<%(entry)s.*>(.*)</%(entry)s>" % { "entry": entry }, reply_data)
            if element:
                self.systeminfo[entry] = function and function(element[0]) or element[0]
        
        return self
    
    def put_in_envelope(self, payload):
        return '<SOAP-ENV:Envelope xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" '\
           'xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ZSI="http://www.zolera.com/schemas/ZSI/" '\
           'xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/" xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '\
           'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'\
           '<SOAP-ENV:Header></SOAP-ENV:Header><SOAP-ENV:Body xmlns:ns1="urn:vim25">' + payload + '</SOAP-ENV:Body></SOAP-ENV:Envelope>'

    def get_pattern(self, pattern, line):
        if not line:
            return []
        p = re.compile(pattern, re.MULTILINE)
        return p.findall(line)

    def query_target(self, payload, payload_params=None):
        if not self.__connection:
            self.connect()

        if payload_params is None:
            payload_params = {}

        # Finalize payload
        payload_params.update(self.systeminfo)
        soapdata = self.put_in_envelope(payload)
        soapdata = soapdata % payload_params

        params = {}
        headers = {}

        def init_headers(soapdata):
            headers["Content-Length"] = "%d" % len(soapdata)
            headers["Content-Type"]   = 'text/xml; charset="utf-8"'
            headers["SOAPAction"]     = "urn:vim25/5.0"
            headers["User-Agent"]     = "VMware VI Client/5.0.0"
            #if target_cookie:
            #    target_connection.putheader("Cookie", target_cookie)
        init_headers(soapdata)

        response_data = []

        time_sent = time.time()
        self.__connection.request("POST", "/sdk", soapdata, headers)

        def check_not_authenticated(text):
            if "NotAuthenticatedFault" in str(text):
                raise QueryServerException("No longer authenticated")

        response = self.__connection.getresponse()
        if not response.status in (200, 201):
            print(response)
        response_data.append(response.read())

        check_not_authenticated(response_data[0][:512])

        while True:
            # Look for a <token>0</token> field.
            # If it exists not all data was transmitted and we need to start a
            # ContinueRetrievePropertiesExResponse query...
            token = re.findall(r"<token>(.*)</token>", response_data[-1][:512].decode("utf-8"))
            if token:
                payload_params.update({"token": token[0]})
                soapdata = self.put_in_envelope(xml_continuetoken) % payload_params
                init_headers(soapdata)
                self.__connection.send(soapdata)
                response = self.__connection.getresponse()
                response_data.append(response.read())
                check_not_authenticated(response_data[-1][:512])
            else:
                break

        time_response = time.time()

        return response.status, response.reason, response.msg, "".join(response_data[0].decode("utf-8"))
        
    #
    #
    #
    xml_systeminfo = '<ns1:RetrieveServiceContent xsi:type="ns1:RetrieveServiceContentRequestType">' \
         '<ns1:_this type="ServiceInstance">ServiceInstance</ns1:_this></ns1:RetrieveServiceContent>'
    
    xml_continuetoken = '<ns1:ContinueRetrievePropertiesEx xsi:type="ns1:ContinueRetrievePropertiesExRequestType">' \
         '<ns1:_this type="PropertyCollector">%(propertyCollector)s</ns1:_this><ns1:token>%(token)s</ns1:token></ns1:ContinueRetrievePropertiesEx>'

class QueryServerException(Exception):
    pass

# ---------------------------------------
#
# Main processing
#
# ---------------------------------------

def main(args):
    """ Main entry point of the app """
    print(args)

    try:
        #global target_connection
        #global target_systeminfo

        t = TargetConnection(args.target)
        t.connect()

        # print(t.retrieve_systeminfo())
        print(t.systeminfo)

    except expression as identifier:
        raise
    finally:
        t.close()
    


if __name__ == "__main__":
    PARSER = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter)

    PARSER.add_argument(
        "-u",
        "--user",
        type=str,
        help="User for connection")

    PARSER.add_argument(
        "-s",
        "--secret",
        type=str,
        help="Secret for connection")

    PARSER.add_argument(
        "-t",
        "--target",
        type=str,
        required=True,
        help="Target address for connection")

    PARSER.add_argument(
        "-p",
        "--port",
        type=int,
        default=443,
        help="Target port for connection")

    PARSER.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout for target connection")

    # Required positional argument
    PARSER.add_argument(
        "-q",
        "--query",
        type=str,
        choices=["all", "about"],
        default="all",
        help="Defines which values are reported back. Defaults to ALL stats.\n"
        "all        > all available stats are reported back\n"
        "about      > about information")

    PARSER.add_argument(
        "--json",
        action="store_true",
        help="Output stats as json.")

    PARSER.add_argument(
        "--cert-check",
        action="store_true",
        help="Ignore certificate checks.")

    # Optional verbosity counter (eg. -v, -vv, -vvv, etc.)
    PARSER.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Verbosity (-v, -vv, etc)")

    # Specify output of "--version"
    PARSER.add_argument(
        "--version",
        action="version",
        version="%(prog)s (version {version})".format(version=__version__))

    ARGS = PARSER.parse_args()
    main(ARGS)
