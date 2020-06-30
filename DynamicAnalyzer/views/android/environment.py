# -*- coding: utf_8 -*-
"""Dynamic Analyzer Helpers."""
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time

from django.conf import settings

from OpenSSL import crypto
from termcolor import colored

from DynamicAnalyzer.tools.webproxy import (
    get_ca_file,
    start_proxy,
    stop_httptools,
)

from MobSF.utils import (
    get_adb,
    get_device,
    get_proxy_ip,
    is_file_exists,
    python_list,
)

from StaticAnalyzer.models import StaticAnalyzerAndroid

logger = logging.getLogger(__name__)
ANDROID_API_SUPPORTED = 28


class Environment:

    def __init__(self, name=None):
        self.name = name
        self.tools_dir = settings.TOOLS_DIR
        self.identifier = None

    def wait(self, sec):
        """Wait in Seconds."""
        logger.info('Waiting for %s seconds...', str(sec))
        time.sleep(sec)

    def check_connect_error(self, output):
        """Check if connect failed."""
        if b'unable to connect' in output or b'failed to connect' in output:
            logger.error('%s', output.decode('utf-8').replace('\n', ''))
            return False
        return True

    def run_subprocess_verify_output(self, command):
        """Run subprocess and verify execution."""
        out = subprocess.check_output(command)
        self.wait(2)
        return self.check_connect_error(out)

    def avd_reference_name(self):
        if self.name == settings.NAME_GENY_0_DUP:
            return [settings.NAME_GENY_0, settings.NAME_GENY_0_DUP]
        elif self.name == settings.NAME_GENY_1_DUP:
            return [settings.NAME_GENY_1, settings.NAME_GENY_1_DUP]
        elif self.name == settings.NAME_GENY_2_DUP:
            return [settings.NAME_GENY_2, settings.NAME_GENY_2_DUP]
        elif self.name == settings.NAME_GENY_3_DUP:
            return [settings.NAME_GENY_3, settings.NAME_GENY_3_DUP]
        elif self.name == settings.NAME_GENY_4_DUP:
            return [settings.NAME_GENY_4, settings.NAME_GENY_4_DUP]

    def connect_n_mount(self):
        """Test ADB Connection."""
        self.adb_command(['kill-server'])
        self.adb_command(['start-server'])
        logger.info('ADB Restarted')
        self.wait(2)
        logger.info('Connecting to Android %s', self.name)

        toolsdir = os.path.join(settings.BASE_DIR, 'DynamicAnalyzer/tools/')  # TOOLS DIR
        names = self.avd_reference_name()
        self.refresh_avd(names[0], names[1])

        # if not self.run_subprocess_verify_output([get_adb(),
        #                                           'connect',
        #                                           self.identifier]):
        #     return False
        # logger.info('Restarting ADB Daemon as root')
        # if not self.run_subprocess_verify_output([get_adb(), 'root']):
        #     return False
        # logger.info('Reconnecting to Android Device')
        # # connect again with root adb
        # if not self.run_subprocess_verify_output([get_adb(),
        #                                           'connect',
        #                                           self.identifier]):
        #     return False
        # # identify environment
        # runtime = self.get_environment()
        # if runtime == 'emulator':
        #     logger.info('Found Android Studio Emulator')
        #     # mount system
        #     logger.info('Remounting')
        #     self.adb_command(['remount'])
        # elif runtime == 'genymotion':
        #     logger.info('Found Genymotion x86 VM')
        #     # mount system
        #     logger.info('Remounting /system')
        #     self.adb_command(['mount', '-o',
        #                       'rw,remount', '/system'], True)
        # else:
        #     logger.error('Only Genymotion VM/Android Studio Emulator'
        #                  ' is supported')
        #     return False
        # logger.info('Performing System check')
        # if not self.system_check(runtime):
        #     return False
        return True

    def adb_command(self, cmd_list, shell=False, silent=False):
        """ADB Command wrapper."""
        if self.identifier is None:
            args = [get_adb()]
        else:
            args = [get_adb(),
                    '-s',
                    self.identifier]
        if shell:
            args += ['shell']
        args += cmd_list

        try:
            result = subprocess.check_output(args)
            return result
        except Exception:
            if not silent:
                logger.exception('Error Running ADB Command')
            return None

    def dz_cleanup(self, bin_hash):
        """Clean up before Dynamic Analysis."""
        # Delete ScreenStream Cache
        screen_file = os.path.join(settings.SCREEN_DIR, 'screen.png')
        if os.path.exists(screen_file):
            os.remove(screen_file)
        # Delete Contents of Screenshot Dir
        screen_dir = os.path.join(
            settings.UPLD_DIR, bin_hash + '/screenshots-apk/')
        if os.path.isdir(screen_dir):
            shutil.rmtree(screen_dir)
        else:
            os.makedirs(screen_dir)

    def configure_proxy(self, project):
        """HTTPS Proxy."""
        self.install_mobsf_ca('install')
        proxy_port = settings.PROXY_PORT
        logger.info('Starting HTTPs Proxy on %s', proxy_port)
        stop_httptools(proxy_port)
        start_proxy(proxy_port, project)

    def install_mobsf_ca(self, action):
        """Install or Remove MobSF Root CA."""
        mobsf_ca = get_ca_file()
        ca_file = None
        if is_file_exists(mobsf_ca):
            ca_construct = '{}.0'
            pem = open(mobsf_ca, 'rb').read()
            ca_obj = crypto.load_certificate(crypto.FILETYPE_PEM, pem)
            ca_file_hash = hex(ca_obj.subject_name_hash()).lstrip('0x')
            ca_file = os.path.join('/system/etc/security/cacerts/',
                                   ca_construct.format(ca_file_hash))
        else:
            logger.warning('mitmproxy root CA is not generated yet.')
            return
        if action == 'install':
            logger.info('Installing MobSF RootCA')
            self.adb_command(['push',
                              mobsf_ca,
                              ca_file])
            self.adb_command(['chmod',
                              '644',
                              ca_file], True)
        elif action == 'remove':
            logger.info('Removing MobSF RootCA')
            self.adb_command(['rm',
                              ca_file], True)
        # with a high timeout afterwards

    def set_global_proxy(self, version):
        """Set Global Proxy on device."""
        # Android 4.4+ supported
        proxy_ip = None
        proxy_port = settings.PROXY_PORT
        if version < 5:
            proxy_ip = get_proxy_ip(self.identifier)
        else:
            proxy_ip = settings.PROXY_IP
        if proxy_ip:
            if version < 4.4:
                logger.warning('Please set Android VM proxy as %s:%s',
                               proxy_ip, proxy_port)
                return
            logger.info('Setting Global Proxy for Android VM')
            self.adb_command(
                ['settings',
                 'put',
                 'global',
                 'http_proxy',
                 '{}:{}'.format(proxy_ip, proxy_port)], True)

    def unset_global_proxy(self):
        """Unset Global Proxy on device."""
        logger.info('Removing Global Proxy for Android VM')
        self.adb_command(
            ['settings',
             'delete',
             'global',
             'http_proxy'], True)
        self.adb_command(
            ['settings',
             'delete',
             'global',
             'global_http_proxy_host'], True)
        self.adb_command(
            ['settings',
             'delete',
             'global',
             'global_http_proxy_port'], True)

    def enable_adb_reverse_tcp(self, version):
        """Enable ADB Reverse TCP for Proxy."""
        # Androd 5+ supported
        if not version >= 5:
            return
        proxy_port = settings.PROXY_PORT
        logger.info('Enabling ADB Reverse TCP on %s', proxy_port)
        tcp = 'tcp:{}'.format(proxy_port)
        try:
            proc = subprocess.Popen([get_adb(),
                                     '-s', self.identifier,
                                     'reverse', tcp, tcp],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            _, stderr = proc.communicate()
            if b'error: closed' in stderr:
                logger.warning('ADB Reverse TCP works only on'
                               ' Android 5.0 and above. Please '
                               'configure a reachable IP Address'
                               ' in Android proxy settings.')
            elif stderr:
                logger.error(stderr.decode('utf-8').replace('\n', ''))
        except Exception:
            logger.exception('Enabling ADB Reverse TCP')

    def start_clipmon(self):
        """Start Clipboard Monitoring."""
        logger.info('Starting Clipboard Monitor')
        args = ['am', 'startservice',
                'opensecurity.clipdump/.ClipDumper']
        self.adb_command(args, True)

    def get_screen_res(self):
        """Get Screen Resolution of Android Instance."""
        logger.info('Getting screen resolution')
        try:
            resp = self.adb_command(['dumpsys', 'window'], True)
            scn_rgx = re.compile(r'mUnrestrictedScreen=\(0,0\) .*')
            scn_rgx2 = re.compile(r'mUnrestricted=\[0,0\]\[.*\]')
            match = scn_rgx.search(resp.decode('utf-8'))
            if match:
                screen_res = match.group().split(' ')[1]
                width, height = screen_res.split('x', 1)
                return width, height
            match = scn_rgx2.search(resp.decode('utf-8'))
            if match:
                res = match.group().split('][')[1].replace(']', '')
                width, height = res.split(',', 1)
                return width, height
            else:
                logger.error('Error getting screen resolution')
        except Exception:
            logger.exception('Getting screen resolution')
        return '1440', '2560'

    def screen_shot(self, outfile):
        """Take Screenshot."""
        self.adb_command(['screencap',
                          '-p',
                          '/data/local/screen.png'], True)
        self.adb_command(['pull',
                          '/data/local/screen.png',
                          outfile])

    def screen_stream(self):
        """Screen Stream."""
        self.adb_command(['screencap',
                          '-p',
                          '/data/local/stream.png'],
                         True)
        self.adb_command(['pull',
                          '/data/local/stream.png',
                          '{}screen.png'.format(settings.SCREEN_DIR)])

    def android_component(self, bin_hash, comp):
        """Get APK Components."""
        anddb = StaticAnalyzerAndroid.objects.filter(MD5=bin_hash)
        resp = []
        if comp == 'activities':
            resp = python_list(anddb[0].ACTIVITIES)
        elif comp == 'receivers':
            resp = python_list(anddb[0].RECEIVERS)
        elif comp == 'providers':
            resp = python_list(anddb[0].PROVIDERS)
        elif comp == 'services':
            resp = python_list(anddb[0].SERVICES)
        elif comp == 'libraries':
            resp = python_list(anddb[0].LIBRARIES)
        elif comp == 'exported_activities':
            resp = python_list(anddb[0].EXPORTED_ACTIVITIES)
        return '\n'.join(resp)

    def get_environment(self):
        """Identify the environment."""
        out = self.adb_command(['getprop',
                                'ro.boot.serialno'], True)
        out += self.adb_command(['getprop',
                                 'ro.serialno'], True)
        out += self.adb_command(['getprop',
                                 'ro.build.user'], True)
        out += self.adb_command(['getprop',
                                 'ro.manufacturer.geny-def'], True)
        out += self.adb_command(['getprop',
                                 'ro.product.manufacturer.geny-def'], True)
        ver = self.adb_command(['getprop',
                                'ro.genymotion.version'],
                               True).decode('utf-8', 'ignore')
        if b'EMULATOR' in out:
            return 'emulator'
        elif (b'genymotion' in out.lower()
              or any(char.isdigit() for char in ver)):
            return 'genymotion'
        else:
            return ''

    def get_android_version(self):
        """Get Android version."""
        out = self.adb_command(['getprop',
                                'ro.build.version.release'], True)
        and_version = out.decode('utf-8').rstrip()
        if and_version.count('.') > 1:
            and_version = and_version.rsplit('.', 1)[0]
        if and_version.count('.') > 1:
            and_version = and_version.split('.', 1)[0]
        return float(and_version)

    def get_android_arch(self):
        """Get Android Architecture."""
        out = self.adb_command([
            'getprop',
            'ro.product.cpu.abi'], True)
        return out.decode('utf-8').rstrip()

    def system_check(self, runtime):
        """Check if /system is writable."""
        try:
            try:
                out = self.adb_command([
                    'getprop',
                    'ro.build.version.sdk'], True)
                if out:
                    api = int(out.decode('utf-8').strip())
                    logger.info('Android API Level '
                                'identified as %s', api)
                    if api > ANDROID_API_SUPPORTED:
                        logger.error('This API Level is not supported'
                                     ' for Dynamic Analysis.')
                        return False
            except Exception:
                pass
            err_msg = ('VM\'s /system is not writable. '
                       'This VM cannot be used for '
                       'Dynamic Analysis.')
            proc = subprocess.Popen([get_adb(),
                                     '-s', self.identifier,
                                     'shell',
                                     'touch',
                                     '/system/test'],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            _, stderr = proc.communicate()
            if b'Read-only' in stderr:
                logger.error(err_msg)
                if runtime == 'emulator':
                    logger.error('Please start the AVD as per '
                                 'MobSF documentation!')
                return False
        except Exception:
            logger.error(err_msg)
            return False
        return True

    def launch_n_capture(self, package, activity, outfile):
        """Launch and Capture Activity."""
        self.adb_command(['am',
                          'start',
                          '-n',
                          package + '/' + activity], True)
        self.wait(3)
        self.screen_shot(outfile)
        logger.info('Activity screenshot captured')
        logger.info('Stopping app')
        self.adb_command(['am', 'force-stop', package], True)

    def is_mobsfyied(self, android_version):
        """Check is Device is MobSFyed."""
        logger.info('Environment MobSFyed Check')
        if android_version < 5:
            agent_file = '.mobsf-x'
            agent_str = b'MobSF-Xposed'
        else:
            agent_file = '.mobsf-f'
            agent_str = b'MobSF-Frida'
        try:
            out = subprocess.check_output(
                [get_adb(),
                 '-s', self.identifier,
                 'shell',
                 'cat',
                 '/system/' + agent_file])
            if agent_str not in out:
                return False
        except Exception:
            return False
        return True

    def mobsfy_init(self):
        """Init MobSFy."""
        version = self.get_android_version()
        logger.info('Android Version identified as %s', version)
        try:
            if version < 5:
                self.xposed_setup(version)
                self.mobsf_agents_setup('xposed')
            else:
                self.frida_setup()
                self.mobsf_agents_setup('frida')
            logger.info('MobSFying Completed!')
            return version
        except Exception:
            logger.exception('Failed to MobSFy Android Instance')
            return False

    def mobsf_agents_setup(self, agent):
        """Setup MobSF agents."""
        # Install MITM RootCA
        self.install_mobsf_ca('install')
        # Install MobSF Agents
        mobsf_agents = 'onDevice/mobsf_agents/'
        clip_dump = os.path.join(self.tools_dir,
                                 mobsf_agents,
                                 'ClipDump.apk')
        logger.info('Installing MobSF Clipboard Dumper')
        self.adb_command(['install', '-r', clip_dump])
        if agent == 'frida':
            agent_file = '.mobsf-f'
        else:
            agent_file = '.mobsf-x'
        mobsf_env = os.path.join(self.tools_dir,
                                 mobsf_agents,
                                 agent_file)
        self.adb_command(['push', mobsf_env, '/system/' + agent_file])

    def xposed_setup(self, android_version):
        """Setup Xposed."""
        xposed_dir = 'onDevice/xposed/'
        xposed_modules = xposed_dir + 'modules/'
        if android_version < 5:
            logger.info('Installing Xposed for Kitkat and below')
            xposed_apk = os.path.join(self.tools_dir,
                                      xposed_dir,
                                      'Xposed.apk')
            hooks = os.path.join(self.tools_dir,
                                 xposed_modules,
                                 'hooks.json')
            droidmon = os.path.join(self.tools_dir,
                                    xposed_modules,
                                    'Droidmon.apk')
            logger.info('Installing Droidmon API Analyzer')
            self.adb_command(['install', '-r', droidmon])
            logger.info('Copying Droidmon hooks config')
            self.adb_command(['push', hooks, '/data/local/tmp/'])
        else:
            logger.info('Installing Xposed for Lollipop and above')
            xposed_apk = os.path.join(self.tools_dir,
                                      xposed_dir,
                                      'XposedInstaller_3.1.5.apk')
        self.adb_command(['install', '-r', xposed_apk])
        # Xposed Modules and Support Files
        justrustme = os.path.join(self.tools_dir,
                                  xposed_modules,
                                  'JustTrustMe.apk')
        rootcloak = os.path.join(self.tools_dir,
                                 xposed_modules,
                                 'RootCloak.apk')
        proxyon = os.path.join(self.tools_dir,
                               xposed_modules,
                               'mobi.acpm.proxyon_v1_419b04.apk')
        sslunpin = os.path.join(self.tools_dir,
                                xposed_modules,
                                'mobi.acpm.sslunpinning_v2_37f44f.apk')
        bluepill = os.path.join(self.tools_dir,
                                xposed_modules,
                                'AndroidBluePill.apk')
        logger.info('Installing JustTrustMe')
        self.adb_command(['install', '-r', justrustme])
        logger.info('Installing SSLUnpinning')
        self.adb_command(['install', '-r', sslunpin])
        logger.info('Installing ProxyOn')
        self.adb_command(['install', '-r', proxyon])
        logger.info('Installing RootCloak')
        self.adb_command(['install', '-r', rootcloak])
        logger.info('Installing Android BluePill')
        self.adb_command(['install', '-r', bluepill])
        logger.info('Launching Xposed Framework.')
        xposed_installer = ('de.robv.android.xposed.installer/'
                            'de.robv.android.xposed.installer.'
                            'WelcomeActivity')
        self.adb_command(['am', 'start', '-n',
                          xposed_installer], True)

    def frida_setup(self):
        """Setup Frida."""
        frida_arch = None
        frida_version = '12.9.8'
        frida_dir = 'onDevice/frida/'
        arch = self.get_android_arch()
        logger.info('Android OS architecture identified as %s', arch)
        if arch in ['armeabi-v7a', 'armeabi']:
            frida_arch = 'arm'
        elif arch == 'arm64-v8a':
            frida_arch = 'arm64'
        elif arch == 'x86':
            frida_arch = 'x86'
        elif arch == 'x86_64':
            frida_arch = 'x86_64'
        else:
            logger.error('Make sure a Genymotion Android x86 VM'
                         ' or Android Studio Emulator'
                         ' instance is running')
            return
        frida_bin = 'frida-server-{}-android-{}'.format(
            frida_version,
            frida_arch)
        frida_path = os.path.join(self.tools_dir,
                                  frida_dir,
                                  frida_bin)
        logger.info('Copying frida server for %s', frida_arch)
        self.adb_command(['push', frida_path, '/system/fd_server'])
        self.adb_command(['chmod', '755', '/system/fd_server'], True)

    def run_frida_server(self):
        """Start Frida Server."""
        check = self.adb_command(['ps'], True)
        if b'fd_server' in check:
            logger.info('Frida Server is already running')
            return

        def start_frida():
            fnull = open(os.devnull, 'w')
            argz = [get_adb(),
                    '-s',
                    self.identifier,
                    'shell',
                    '/system/fd_server']
            subprocess.call(argz, stdout=fnull, stderr=subprocess.STDOUT)

        trd = threading.Thread(target=start_frida)
        trd.daemon = True
        trd.start()
        logger.info('Starting Frida Server')
        logger.info('Waiting for 2 seconds...')
        time.sleep(2)

    def start_avd(self, avd_name):
        """Start AVD"""
        print("\n[INFO] Starting MobSF Emulator")
        FNULL = open(os.devnull, 'w')
        try:
            args = [
                settings.PATH_GMTOOL,
                "admin",
                "start",
                avd_name
                # "Google Pixel 3"
            ]

            print("TOTO")

            if platform.system() == 'Darwin':
                # There is a strage error in mac with the dyld one in a while..
                # this should fix it..
                if 'DYLD_FALLBACK_LIBRARY_PATH' in os.environ.keys():
                    del os.environ['DYLD_FALLBACK_LIBRARY_PATH']

            subprocess.call(args, stderr=FNULL)

            print("TOTO2")

            time.sleep(10)
            self.identifier = get_device(avd_name)
            print(str(self.identifier))
            if not self.identifier:
                time.sleep(10)
                self.identifier = get_device(self.name)
            time.sleep(5)
            args = ["adb",
                    "-s",
                    self.identifier,
                    "wait-for-device"]

        except:
            print(colored("[ERROR] Starting MobSF Emulator", "red"))

    def stop_avd(self, name):
        """Stop AVD"""
        print("\n[INFO] Stopping MobSF Emulator")
        try:
            # adb -s emulator-xxxx emu kill
            FNULL = open(os.devnull, 'w')
            # args = [adb, '-s', self.identifier, 'emu', 'kill']
            args = [settings.PATH_GMTOOL,
                    "admin",
                    "stop",
                    name
                    # "Google Pixel 3"
                    ]
            subprocess.call(args, stderr=FNULL)
            # os.system("adb emu kill")
        except:
            print(colored("[ERROR] Stopping MobSF Emulator", "red"))

    def delete_avd(self, avd_name):
        """Delete AVD"""
        print("\n[INFO] Deleting emulator files")
        try:
            # config_file = os.path.join(avd_path, avd_name + '.ini')
            # if os.path.exists(config_file):
            #     os.remove(config_file)
            # '''
            # # todo: Sometimes there is an error here because of the locks that avd
            # # does - check this out
            # '''
            # avd_folder = os.path.join(avd_path, avd_name + '.avd')
            # if os.path.isdir(avd_folder):
            #     shutil.rmtree(avd_folder)
            FNULL = open(os.devnull, 'w')
            # args = [adb, '-s', self.identifier, 'emu', 'kill']
            args = [settings.PATH_GMTOOL,
                    "admin",
                    "delete",
                    avd_name,
                    ]
            subprocess.call(args, stderr=FNULL)
        except:
            print(colored("[ERROR] Deleting emulator files", "red"))

    def duplicate_avd(self, reference_name, dup_name):
        """Duplicate AVD"""
        print("\n[INFO] Duplicating MobSF Emulator")
        try:
            FNULL = open(os.devnull, 'w')
            # args = [adb, '-s', self.identifier, 'emu', 'kill']
            args = [settings.PATH_GMTOOL,
                    "admin",
                    "clone",
                    reference_name,
                    dup_name,
                    "sdk_path=/usr/lib/android-sdk/"
                    ]
            subprocess.call(args, stderr=FNULL)
        except:
            print(colored("[ERROR] Duplicating MobSF Emulator", "red"))

    def refresh_avd(self, reference_name, dup_name):
        """Refresh AVD"""
        print("\n[INFO] Refreshing MobSF Emulator")
        try:
            # Stop existing emulator on the spesified port
            self.stop_avd(dup_name)

            # Windows has annoying lock system, it takes time for it to remove the locks after we stopped the emulator
            if platform.system() == 'Windows':
                time.sleep(3)

            # Delete old emulator
            self.delete_avd(dup_name)

            # Copy and replace the contents of the reference machine
            self.duplicate_avd(reference_name, dup_name)

            # Start emulator
            self.start_avd(dup_name)
        except:
            print(colored("[ERROR] Refreshing MobSF VM", "red"))
