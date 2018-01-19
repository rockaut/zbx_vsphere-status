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

    user      = None
    secret    = None

    last_error = None

    host_cookie_path = "~/tmp/zbx/vsphere"
    host_cookie_file = None
    last_update = None
    server_cookie = None

    __connection = None
    __cookie     = None

    __auth_retry = 0

    licenses = []
    systeminfo = {}
    hostsystems = {}
    datastores = {}
    hostdetails = {}

    opt_direct = True
    opt_spaces = "underscore"

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

    def __init__(self, hostname, user, secret):
        self.hostname = hostname
        self.user     = user
        self.secret   = secret
        self.host_cookie_path = os.path.expanduser(self.host_cookie_path)
        self.host_cookie_file = "%s/cookie.%s" % (self.host_cookie_path, self.hostname)

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

            if not self.systeminfo:
                raise TargetConnection.WebApiException("Unable to retrieve data from Web API")
        except:
            self.close()
            raise
    
    def close(self):
        if self.__connection:
            self.__connection.close()

    def retrieve_systeminfo(self):
        """Retrieve basic data, which requires no login"""
        payload = self.__xml_systeminfo
        reply_code, reply_msg, reply_headers, reply_data = self.query_target(payload)

        for entry, function in self.systemfields:
            element = self.get_pattern("<%(entry)s.*>(.*)</%(entry)s>" % { "entry": entry }, reply_data)
            if element:
                self.systeminfo[entry] = function and function(element[0]) or element[0]

        self.opt_direct = ( self.systeminfo['apiType'] == 'HostAgent' )
        
        return self

    def retrieve_hostsystems(self):
        payload = self.__xml_hostsystems

        reply_code, reply_msg, reply_headers, reply_data = self.query_target(self.__xml_hostsystems)
        elements = self.get_pattern('<obj type="HostSystem">(.*?)</obj>.*?<val xsi:type="xsd:string">(.*?)</val>', reply_data)
        for hostsystem, name in elements:
            self.hostsystems[hostsystem] = name
        
        self.__hostdetails()

        return self

    def retrieve_licenses(self):
        self.licenses = []
        reply_code, reply_msg, reply_headers, reply_data = self.query_target(self.__xml_licensesused)

        root_node     = minidom.parseString(reply_data)
        licenses_node = root_node.getElementsByTagName("LicenseManagerLicenseInfo")
        for license_node in licenses_node:
            total = license_node.getElementsByTagName("total")[0].firstChild.data
            if total == "0":
                continue
            name  = license_node.getElementsByTagName("name")[0].firstChild.data
            used  = license_node.getElementsByTagName("used")[0].firstChild.data
            lic = {
                'name': name,
                'used': used,
                'total': total
            }
            self.licenses += [ lic ]
        
        return self

    def __hostdetails(self):
        hostsystems_properties = {}
        hostsystems_sensors    = {}

        self.hostdetails = {}

        # Propsets
        reply_code, reply_msg, reply_headers, reply_data = self.query_target(self.__xml_hostdetails)
        hostsystems_objects = self.get_pattern('<objects>(.*?)</objects>', reply_data)

        for entry in hostsystems_objects:
            hostname = self.get_pattern('<obj type="HostSystem">(.*)</obj>', entry[:512])[0]
            hostsystems_properties[hostname] = {}
            hostsystems_sensors[hostname]    = {}

            current_propname = ""

            def eval_sensor_info(sensor_propset):
                sensor_pattern = ""
                for key in [ "name", "label", "summary", "key", "currentReading",
                                "unitModifier", "baseUnits", "sensorType" ]:
                    sensor_pattern += "<%(name)s>(.*?)</%(name)s>.*?" % { "name": key}

                sensor_data =  self.get_pattern(sensor_pattern, sensor_propset)
                for name, label, summary, key, currentReading, unitModifier, baseUnits, sensorType in sensor_data:
                    hostsystems_sensors[hostname][name] = { "name": name, "label": label, "summary": summary, "key": key,
                                                            "currentReading": currentReading, "unitModifier": unitModifier,
                                                            "baseUnits": baseUnits, "sensorType": sensorType }

            def eval_hardwarestatus_info(sensor_propset):
                sensor_pattern = ""
                for key in [ "name", "label", "summary", "key" ]:
                    sensor_pattern += "<%(name)s>(.*?)</%(name)s>.*?" % { "name": key}

                sensor_data = self.get_pattern(sensor_pattern, sensor_propset)
                for name, label, summary, key in sensor_data:
                    hostsystems_sensors[hostname][name] = { "name": name, "label": label, "summary": summary, "key": key }

            def eval_multipath_state(multipath_propset):
                multipaths = self.get_pattern("<name>(.*?)</name><pathState>(.*?)</pathState>", value)
                for mp_name, mp_state in multipaths:
                    hba_details = mp_name.split(":")
                    hba_name = hba_details[0]
                    hba_num = int(hba_name.replace("vmhba", ""))
                    lun_id = hba_details[-1]

                    if hba_num >= 32 and hba_num % 64 < 33:
                        lun_id = "%s pseudo-logical" % lun_id
                    elif hba_num >= 32 and hba_num % 64 > 32:
                        lun_id = "%s logical" % lun_id
                    else:
                        lun_id = "%s physical" % lun_id
                    
                    hostsystems_properties[hostname].setdefault(current_propname, {}).setdefault(lun_id, {}).setdefault(mp_state, 0)
                    hostsystems_properties[hostname][current_propname][lun_id].setdefault("adapters", [])
                    hostsystems_properties[hostname][current_propname][lun_id][mp_state] += 1
                    hostsystems_properties[hostname][current_propname][lun_id]["adapters"].append(mp_name)

            def eval_propset_block(elements, id_key, propset):
                pattern = ""
                for key in elements:
                    pattern += "<%(name)s>(.*?)</%(name)s>.*?" % { "name": key}

                data = self.get_pattern(pattern, propset)
                for match_groups in data:
                    entries = dict(zip(elements, match_groups))
                    for key, value in entries.items():
                        hostsystems_properties[hostname].setdefault("%s.%s.%s" % \
                            (current_propname, key, entries[id_key]), []).append(value)

            def eval_cpu_pkg(cpu_pkg_propset):
                eval_propset_block( [ "index", "vendor", "hz", "busHz", "description" ], "index", cpu_pkg_propset)

            def eval_pci_device(pci_propset):
                eval_propset_block( [ "id", "vendorName", "deviceName" ], "id", pci_propset)

            def eval_systeminfo_other(otherinfo_propset):
                data       = self.get_pattern("<identifierValue>(.*?)</identifierValue>.*?<key>(.*?)</key>", otherinfo_propset)
                keys_index = {}

                for value, key in data:
                    idx = 0
                    if key in keys_index:
                        keys_index[key] = keys_index[key] + 1
                        idx = keys_index[key]
                    hostsystems_properties[hostname]["hardware.systemInfo.otherIdentifyingInfo.%s.%d" % (key, idx)] = [ value ]
                    keys_index[key] = idx

            eval_functions = {
                "config.multipathState.path"                                      : eval_multipath_state,
                "runtime.healthSystemRuntime.systemHealthInfo.numericSensorInfo"  : eval_sensor_info,
                "runtime.healthSystemRuntime.hardwareStatusInfo.storageStatusInfo": eval_hardwarestatus_info,
                "runtime.healthSystemRuntime.hardwareStatusInfo.cpuStatusInfo"    : eval_hardwarestatus_info,
                "runtime.healthSystemRuntime.hardwareStatusInfo.memoryStatusInfo" : eval_hardwarestatus_info,
                "hardware.cpuPkg"                                                 : eval_cpu_pkg,
                "hardware.pciDevice"                                              : eval_pci_device,
                "hardware.systemInfo.otherIdentifyingInfo"                        : eval_systeminfo_other,
            }

            elements = self.get_pattern('<propSet><name>(.*?)</name><val.*?>(.*?)</val></propSet>', entry)
            for current_propname, value in elements:
                if eval_functions.get(current_propname):
                    eval_functions[current_propname](value)
                else:
                    hostsystems_properties[hostname].setdefault(current_propname, []).append(value)

        for hostname, properties in hostsystems_properties.items():

            self.hostdetails[properties['name'][0]] = {
                'properties': properties,
                #'sensors': hostsystems_sensors[hostname]
            }

    def retrieve_datastores(self):
        self.datastores = {}
        reply_code, reply_msg, reply_headers, reply_data = self.query_target(self.__xml_datastores)
        elements = self.get_pattern('<objects><obj type="Datastore">(.*?)</obj>(.*?)</objects>', reply_data)
        for datastore, content in elements:
            entries = self.get_pattern('<name>(.*?)</name><val xsi:type.*?>(.*?)</val>', content)
            self.datastores[datastore] = {}
            for name, value in entries:
                self.datastores[datastore][name] = value

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
    
    def encode_url(self, text):
        for char, replacement in [ ( "&",  "&amp;"),
                                ( ">",  "&gt;" ),
                                ( "<",  "&lt;"),
                                ( "'",  "&apos;"),
                                ( "\"", "&quot;") ]:
            text = text.replace(char, replacement)
        return text

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
            headers["User-Agent"]     = "Zbx-vSphere-Status"
            if self.server_cookie:
                headers["Cookie"]     = self.server_cookie
        init_headers(soapdata)

        response_data = []

        time_sent = time.time()
        self.__connection.request("POST", "/sdk", soapdata, headers)

        def check_not_authenticated(text, retry):
            if "NotAuthenticatedFault" in str(text):
                raise TargetConnection.QueryServerException("No longer authenticated")
            elif '<fault xsi:type="NotAuthenticated">' in str(text):
                if retry <= 1:
                    self.__removeCookie()
                else:
                    raise TargetConnection.QueryServerException("No longer authenticated")

        response = self.__connection.getresponse()
        response_data.append(response.read())

        retry = 0
        while retry <= 1:
            retry += 1
            check_not_authenticated(response_data[0][:512], retry)

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
    
    def login(self):
        if not os.path.exists(self.host_cookie_path):
            os.makedirs(self.host_cookie_path)
        
        if self.host_cookie_path and os.path.exists(self.host_cookie_file):
            self.last_update = int(os.stat(self.host_cookie_file).st_mtime)
            self.server_cookie = open(self.host_cookie_file, "r").read()
        else:
            payload = self.__xml_login
            reply_code, reply_msg, reply_headers, reply_data = \
                        self.query_target(payload, payload_params = {"username": self.encode_url(self.user),
                                                                "password": self.encode_url(self.secret)})

            if "InvalidLogin" in reply_data:
                self.last_update = "Cannot login to vSphere Server. Login response is not 'OK'. Please check the credentials"
            else:
                self.server_cookie = reply_headers.get("Set-Cookie")
                if self.host_cookie_file and self.server_cookie:
                    cookie_file = open(self.host_cookie_file, "w")
                    os.chmod(self.host_cookie_file, 600)
                    cookie_file.write(self.server_cookie)
                    cookie_file.close()
    
    def logout(self):
        try:
            self.query_target(self.__xml_logout)
            self.__removeCookie()
        except:
            pass

    def __removeCookie(self):
        if self.host_cookie_path and os.path.exists(self.host_cookie_file):
            os.unlink(self.host_cookie_file)
    
    def convert_hostname(self, h):
        if self.opt_spaces == "cut":
            return h.split()[0]
        else:
            return h.replace(" ", "_")
        
    #
    # Additional values for fetching data
    #
    __xml_systeminfo = '<ns1:RetrieveServiceContent xsi:type="ns1:RetrieveServiceContentRequestType">' \
         '<ns1:_this type="ServiceInstance">ServiceInstance</ns1:_this></ns1:RetrieveServiceContent>'
    
    __xml_continuetoken = '<ns1:ContinueRetrievePropertiesEx xsi:type="ns1:ContinueRetrievePropertiesExRequestType">' \
         '<ns1:_this type="PropertyCollector">%(propertyCollector)s</ns1:_this><ns1:token>%(token)s</ns1:token></ns1:ContinueRetrievePropertiesEx>'

    __xml_login = '<ns1:Login xsi:type="ns1:LoginRequestType"><ns1:_this type="SessionManager">%(sessionManager)s</ns1:_this>' \
         '<ns1:userName>%(username)s</ns1:userName><ns1:password>%(password)s</ns1:password></ns1:Login>'

    __xml_logout = '<ns1:Logout xsi:type="ns1:LogoutRequestType">' \
         '<ns1:_this type="SessionManager">%(sessionManager)s</ns1:_this></ns1:Logout>'
    
    __xml_hostsystems = '<ns1:RetrievePropertiesEx xsi:type="ns1:RetrievePropertiesExRequestType">'\
         '<ns1:_this type="PropertyCollector">%(propertyCollector)s</ns1:_this><ns1:specSet>'\
         '<ns1:propSet><ns1:type>HostSystem</ns1:type><ns1:pathSet>name</ns1:pathSet></ns1:propSet>'\
         '<ns1:objectSet><ns1:obj type="Folder">%(rootFolder)s</ns1:obj><ns1:skip>false</ns1:skip>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>visitFolders</ns1:name>'\
           '<ns1:type>Folder</ns1:type><ns1:path>childEntity</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToHf</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToVmf</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>crToH</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>crToRp</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToDs</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>hToVm</ns1:name></ns1:selectSet>'\
         '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToVmf</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>vmFolder</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToDs</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>datastore</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToHf</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>hostFolder</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>crToH</ns1:name><ns1:type>ComputeResource</ns1:type>'\
         '<ns1:path>host</ns1:path><ns1:skip>false</ns1:skip></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>crToRp</ns1:name><ns1:type>ComputeResource</ns1:type>'\
         '<ns1:path>resourcePool</ns1:path><ns1:skip>false</ns1:skip>'\
         '<ns1:selectSet><ns1:name>rpToRp</ns1:name></ns1:selectSet>'\
         '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>rpToRp</ns1:name><ns1:type>ResourcePool</ns1:type>'\
           '<ns1:path>resourcePool</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>rpToRp</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>hToVm</ns1:name><ns1:type>HostSystem</ns1:type>'\
           '<ns1:path>vm</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>rpToVm</ns1:name><ns1:type>ResourcePool</ns1:type>'\
         '<ns1:path>vm</ns1:path><ns1:skip>false</ns1:skip></ns1:selectSet>'\
         '</ns1:objectSet></ns1:specSet><ns1:options></ns1:options></ns1:RetrievePropertiesEx>'
    
    __xml_licensesused = '<ns1:RetrievePropertiesEx xsi:type="ns1:RetrievePropertiesExRequestType">'\
          '<ns1:_this type="PropertyCollector">%(propertyCollector)s</ns1:_this>'\
          '<ns1:specSet>'\
            '<ns1:propSet>'\
              '<ns1:type>LicenseManager</ns1:type>'\
              '<all>0</all>'\
              '<ns1:pathSet>licenses</ns1:pathSet>'\
            '</ns1:propSet>'\
            '<ns1:objectSet>'\
              '<ns1:obj type="LicenseManager">%(licenseManager)s</ns1:obj>'\
            '</ns1:objectSet>'\
          '</ns1:specSet>'\
          '<ns1:options/>'\
        '</ns1:RetrievePropertiesEx>'
    
    __xml_datastores = '<ns1:RetrievePropertiesEx xsi:type="ns1:RetrievePropertiesExRequestType">'\
         '<ns1:_this type="PropertyCollector">%(propertyCollector)s</ns1:_this><ns1:specSet>'\
         '<ns1:propSet><ns1:type>Datastore</ns1:type><ns1:pathSet>name</ns1:pathSet>'\
         '<ns1:pathSet>summary.freeSpace</ns1:pathSet>'\
         '<ns1:pathSet>summary.capacity</ns1:pathSet>'\
         '<ns1:pathSet>summary.uncommitted</ns1:pathSet>'\
         '<ns1:pathSet>summary.url</ns1:pathSet>'\
         '<ns1:pathSet>summary.accessible</ns1:pathSet>'\
         '<ns1:pathSet>summary.type</ns1:pathSet>'\
         '<ns1:pathSet>summary.maintenanceMode</ns1:pathSet></ns1:propSet>'\
         '<ns1:objectSet><ns1:obj type="Folder">%(rootFolder)s</ns1:obj><ns1:skip>false</ns1:skip>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>visitFolders</ns1:name>'\
           '<ns1:type>Folder</ns1:type><ns1:path>childEntity</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToHf</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToVmf</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>crToH</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>crToRp</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToDs</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>hToVm</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToVmf</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>vmFolder</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToDs</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>datastore</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToHf</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>hostFolder</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>crToH</ns1:name><ns1:type>ComputeResource</ns1:type>'\
         '<ns1:path>host</ns1:path><ns1:skip>false</ns1:skip></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>crToRp</ns1:name><ns1:type>ComputeResource</ns1:type>'\
           '<ns1:path>resourcePool</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>rpToRp</ns1:name></ns1:selectSet>'\
         '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>rpToRp</ns1:name><ns1:type>ResourcePool</ns1:type>'\
           '<ns1:path>resourcePool</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>rpToRp</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>hToVm</ns1:name><ns1:type>HostSystem</ns1:type>'\
           '<ns1:path>vm</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>rpToVm</ns1:name><ns1:type>ResourcePool</ns1:type>'\
         '<ns1:path>vm</ns1:path><ns1:skip>false</ns1:skip></ns1:selectSet>'\
         '</ns1:objectSet></ns1:specSet><ns1:options></ns1:options></ns1:RetrievePropertiesEx>'
    
    __xml_hostdetails = '<ns1:RetrievePropertiesEx xsi:type="ns1:RetrievePropertiesExRequestType">' \
         '<ns1:_this type="PropertyCollector">%(propertyCollector)s</ns1:_this><ns1:specSet><ns1:propSet>'\
         '<ns1:type>HostSystem</ns1:type>'\
         '<ns1:pathSet>summary.quickStats.overallMemoryUsage</ns1:pathSet>'\
         '<ns1:pathSet>hardware.cpuPkg</ns1:pathSet>'\
         '<ns1:pathSet>runtime.powerState</ns1:pathSet>'\
         '<ns1:pathSet>summary.quickStats.overallCpuUsage</ns1:pathSet>'\
         '<ns1:pathSet>hardware.biosInfo.biosVersion</ns1:pathSet>'\
         '<ns1:pathSet>hardware.biosInfo.releaseDate</ns1:pathSet>'\
         '<ns1:pathSet>hardware.cpuInfo.hz</ns1:pathSet>'\
         '<ns1:pathSet>hardware.cpuInfo.numCpuThreads</ns1:pathSet>'\
         '<ns1:pathSet>hardware.cpuInfo.numCpuPackages</ns1:pathSet>'\
         '<ns1:pathSet>hardware.cpuInfo.numCpuCores</ns1:pathSet>'\
         '<ns1:pathSet>config.multipathState.path</ns1:pathSet>'\
         '<ns1:pathSet>hardware.systemInfo.model</ns1:pathSet>'\
         '<ns1:pathSet>hardware.systemInfo.uuid</ns1:pathSet>'\
         '<ns1:pathSet>hardware.systemInfo.otherIdentifyingInfo</ns1:pathSet>'\
         '<ns1:pathSet>hardware.systemInfo.vendor</ns1:pathSet>'\
         '<ns1:pathSet>name</ns1:pathSet>'\
         '<ns1:pathSet>overallStatus</ns1:pathSet>'\
         '<ns1:pathSet>runtime.healthSystemRuntime.systemHealthInfo.numericSensorInfo</ns1:pathSet>'\
         '<ns1:pathSet>runtime.healthSystemRuntime.hardwareStatusInfo.storageStatusInfo</ns1:pathSet>'\
         '<ns1:pathSet>runtime.healthSystemRuntime.hardwareStatusInfo.cpuStatusInfo</ns1:pathSet>'\
         '<ns1:pathSet>runtime.healthSystemRuntime.hardwareStatusInfo.memoryStatusInfo</ns1:pathSet>'\
         '<ns1:pathSet>runtime.inMaintenanceMode</ns1:pathSet>'\
         '<ns1:pathSet>hardware.memorySize</ns1:pathSet></ns1:propSet>'\
         '<ns1:objectSet><ns1:obj type="Folder">%(rootFolder)s</ns1:obj><ns1:skip>false</ns1:skip>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>visitFolders</ns1:name>'\
           '<ns1:type>Folder</ns1:type><ns1:path>childEntity</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToHf</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToVmf</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>crToH</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>crToRp</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>dcToDs</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>hToVm</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToVmf</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>vmFolder</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToDs</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>datastore</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>dcToHf</ns1:name><ns1:type>Datacenter</ns1:type>'\
           '<ns1:path>hostFolder</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>crToH</ns1:name><ns1:type>ComputeResource</ns1:type>'\
           '<ns1:path>host</ns1:path><ns1:skip>false</ns1:skip></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>crToRp</ns1:name><ns1:type>ComputeResource</ns1:type>'\
           '<ns1:path>resourcePool</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>rpToRp</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>rpToRp</ns1:name><ns1:type>ResourcePool</ns1:type>'\
           '<ns1:path>resourcePool</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>rpToRp</ns1:name></ns1:selectSet>'\
           '<ns1:selectSet><ns1:name>rpToVm</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>hToVm</ns1:name><ns1:type>HostSystem</ns1:type>'\
           '<ns1:path>vm</ns1:path><ns1:skip>false</ns1:skip>'\
           '<ns1:selectSet><ns1:name>visitFolders</ns1:name></ns1:selectSet></ns1:selectSet>'\
         '<ns1:selectSet xsi:type="ns1:TraversalSpec"><ns1:name>rpToVm</ns1:name><ns1:type>ResourcePool</ns1:type>'\
           '<ns1:path>vm</ns1:path><ns1:skip>false</ns1:skip></ns1:selectSet>'\
         '</ns1:objectSet></ns1:specSet><ns1:options></ns1:options></ns1:RetrievePropertiesEx>'
    #
    # Exception wrapper classes
    #
    class WebApiException(Exception):
        pass

    class QueryServerException(Exception):
        pass

    #
    # end TargetConnection
    #

# ---------------------------------------
#
# Main processing
#
# ---------------------------------------

def main(args):
    """ Main entry point of the app """
    #print(args)

    try:
        #global target_connection
        #global target_systeminfo

        t = TargetConnection(args.target, user=args.user, secret=args.secret)
        t.connect()

        # print(t.retrieve_systeminfo())
        print(t.systeminfo)
        t.login()
        t.retrieve_hostsystems()
        t.retrieve_licenses()
        t.retrieve_datastores()

        print(t.hostsystems)
        print(t.licenses)
        print(t.datastores)
        print(t.hostdetails)

    except:
        raise
    finally:
        if args.logout:
            t.logout()
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
        "--logout",
        action="store_true",
        help="Logout on end of script.")

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
