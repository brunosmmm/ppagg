from util.misc import NodeAddress
from scan import scan_new_node, scan_node_services, scan_node_modules, retrieve_json_data, post_json_data, NodeScanError
import logging
from periodicpy.plugmgr.plugin.exception import ModuleAlreadyLoadedError

class NodeElementError(Exception):
    pass

class PeriodicPiNode(object):
    def __init__(self, node_element, node_address):
        self.element = node_element
        self.addr = NodeAddress(*node_address)
        #initial state
        self.scanned = False
        self.scanned_services = {}
        self.service_drivers = {}
        self.node_plugins = {}
        self.node_plugin_structure = {}

        self.logger = logging.getLogger('ppagg.node-{}'.format(node_element))

    def get_node_element(self):
        return self.element

    def get_node_plugins(self):
        return self.node_plugins

    def get_node_plugin_structure(self, inst_name):
        return self.node_plugin_structure[self.node_plugins[inst_name]]

    def call_plugin_method(self, instance_name, method_name, method_args):
        #do some verification

        if instance_name not in self.node_plugins:
            return None #plugin not loaded

        module_methods = self.node_plugin_structure[self.node_plugins[instance_name]]['module_methods']
        if method_name not in module_methods:
            return None #method does not exist

        for arg in method_args:
            if arg not in module_methods[method_name]['method_args']:
                return None #invalid argument

        for arg_name, arg in module_methods[method_name]['method_args'].iteritems():
            if arg['arg_required'] == True and arg_name not in method_args:
                return None #missing required argument

        #bottle not linking nested dictionaries, undo method_args dictionary
        arg_pairs = []
        for arg_name, arg in method_args.iteritems():
            arg_pairs.append('{}={}'.format(arg_name, arg))

        #call (post)
        try:
            ret = post_json_data(self.addr, 'plugins/{}/{}'.format(instance_name, method_name), {'method_args' : ','.join(arg_pairs)})
        except NodeScanError:
            return None #error while calling


    def get_serializable_dict(self, simple=True):
        ret = {}

        ret['node_addr'] = self.addr.address
        ret['node_port'] = self.addr.port
        ret['node_descr'] = self.description
        ret['node_location'] = self.location
        if simple == False:
            ret['scanned_services'] = self.scanned_services
            ret['driver_instances'] = self.service_drivers

        return ret

    def register_basic_information(self):

        scan_result = scan_new_node(self.addr)

        if scan_result['node_element'] != self.element:
            raise NodeElementError('error while getting node information')

        self.__dict__.update(scan_result)

        self.scanned = True

    def register_node_plugins(self):

        scan_result = scan_node_modules(self.addr)

        #dump json data, analyze
        self.node_plugin_structure = {}
        self.node_plugins = dict(scan_result)

        #only retrieve each kind once
        node_plugin_types = set(scan_result.values())

        for kind in node_plugin_types:
            self.node_plugin_structure[kind] = retrieve_json_data(self.addr,
                                                                  'plugins/{}/structure'.format(kind))

            self.logger.debug('discovered node-side plugin class: {}'.format(kind))

    def register_services(self, available_drivers, driver_manager):

        scan_result = scan_node_services(self.addr)
        self.scanned_services = dict(scan_result)

        for service in scan_result['services']:
            self.logger.debug('discovered service "{}"'.format(service['service_name']))
            if service['enabled'] == False:
                self.logger.debug('service "{}" is disabled'.format(service['service_name']))
                continue

            loaded_mod_id = None
            if service['service_name'] in available_drivers:
                #do stuff!
                self.logger.debug('driver for "{}" is available'.format(service['service_name']))
                try:
                    loaded_mod_id = driver_manager(load_module=[service['service_name'],
                                                                {'server_address':self.addr.address,
                                                                 'server_port':int(service['port']),
                                                                 'attached_node':self.element}])
                except ModuleAlreadyLoadedError:
                    pass
                except TypeError:
                    #load without address (not needed)?
                    try:
                        loaded_mod_id = driver_manager(load_module=[service['service_name'], {'attached_node':self.element}])
                    except Exception:
                        #give up
                        raise

            else:
                self.logger.warn('no driver available for service {}'.format(service['service_name']))

            #save module information
            self.service_drivers[service['service_name']] = loaded_mod_id

    def unregister_services(self, driver_manager):
        for loaded_module in self.service_drivers.values():
            try:
                driver_manager(unload_module=[loaded_module])
            except Exception as e:
                self.logger.warn('could not unload instance "{}": {}'.format(loaded_module, e.message))
