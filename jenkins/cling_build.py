# Build cling in Jenkins.
#
# Axel, 2016-01-05

import sys, os, errno, shutil, tarfile
from subprocess import check_call, call
from datetime import date
from distutils import spawn


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc: # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else: raise

def print_and_call(args, check=True):
    print('Running: ' + str(args))
    sys.stdout.flush()
    if check:
        check_call(args=str(args), shell=True)
    else:
        call(args=str(args), shell=True)


class Builder:
    """Build cling"""

    def printConfig(self):
        print('CONFIGURATION: ' + str(self.__dict__))


    def cmake_build(self, targetname = '', check = True):
        target = ''
        if targetname:
            target = '--target ' + targetname
        print_and_call(self.cmake + ' --build . ' + target + self.parallelFlag, check = check)


    def __init__(self, workspace, label, generatorType, cleanbuild, binaries, buildcause, testcling, testllvmclang):
        """ Parameters:
        workspace: str
          Jenkins workspace directory.
        label: str
          Label of the node this script is running on. 'ubuntu2204' is expected to
          be able to run doxygen and will create source snapshot if binaries are
          requested.
        clean: bool
          remove build and install directories. Overridden to `True` for full
          builds.
        binaries: bool
          whether to publish binaries, source snapshot and doxygen documentation
          to root.cern.ch. Overridden to `True` for full builds, `False` for
          incrementals.
        label: str
        buildcause: str
          Jenkins ROOT_BUILD_CAUSE. Determins build mode. If triggered by:
            - SCM: incremental build. Sets `clean` and `binaries` to `False`.
            - schedule: full build. Sets `clean` and `binaries` to `True`.
        testcling: bool
          whether to run cling's test suite (and fail if it fails)
        testllvmclang: bool
          whether to run llvm's and clang's test suite. clang's test suite is
          known to fail; the outcome of clang's test result is thus ignored when
          determining the outcome of this step. A failure in llvm's test suite
          will fail the build.
        """

        self.today = str(date.today())
        self.workspace = workspace
        self.label = label
        self.generatorType = generatorType
        self.testcling = testcling
        self.testllvmclang = testllvmclang
        self.doxygen = False
        if 'ubuntu22' in self.label:
            self.doxygen = True

        self.parallelFlag = ''
        if generatorType == 'Unix Makefiles':
            self.parallelFlag = ' -- -j8'


        # Build setup (manual, nightly, incremental)
        if type(buildcause) is str and buildcause != 'MANUALTRIGGER':
            # nightly wins, even if there was a commit right before.
            if 'TIMERTRIGGER' in buildcause:
                # nightly build
                binaries = True
                cleanbuild = True
            elif 'SCMTRIGGER' in buildcause:
                # incremental build
                binaries = False
                cleanbuild = False

        self.instdir = 'inst'
        if binaries:
            self.instdir = 'cling_' + self.today + '_' + label
            cleanbuild = True

        if not os.path.isdir('obj'):
            # force configure
            cleanbuild = True

        self.cleanbuild = cleanbuild
        self.binaries = binaries

        self.cmake = spawn.find_executable('cmake3')
        if self.cmake == None:
          self.cmake = spawn.find_executable('cmake')
        if self.cmake == None:
            if self.label == 'cc7':
                self.cmake = '/cvmfs/sft.cern.ch/lcg/contrib/CMake/3.7.0/Linux-x86_64/bin/cmake'
            else:
                self.cmake = '/usr/local/bin/cmake'

        self.printConfig()


    def maybe_clean(self):
        if self.cleanbuild:
            if os.path.isdir(self.instdir):
                shutil.rmtree(self.instdir)
            if os.path.isdir('obj'):
                shutil.rmtree('obj')


    def configure(self):
        if self.cleanbuild:
            doxygen = ''
            if self.doxygen:
                doxygen = ' -DLLVM_ENABLE_DOXYGEN=On -DLLVM_INCLUDE_DOCS=On'
            print_and_call(self.cmake # -G "' + self.generatorType + '"'
                           + ' -DCMAKE_BUILD_TYPE=Release'
                           + ' -DLLVM_BUILD_TOOLS=Off'
                           + ' -DCMAKE_INSTALL_PREFIX=' + self.workspace + '/' + self.instdir
                           + ' -DLLVM_EXTERNAL_PROJECTS=cling -DLLVM_EXTERNAL_CLING_SOURCE_DIR=' + self.workspace + '/cling'
                           + ' -DLLVM_ENABLE_PROJECTS=clang'
                           + ' -DLLVM_TARGETS_TO_BUILD="host;NVPTX"'
                           + ' "-DLLVM_LIT_ARGS=-sv --no-progress-bar --xunit-xml-output=lit-xunit-output.xml"'
                           + doxygen
                           + ' ../src/llvm' )


    def make(self):
        self.cmake_build()
        self.documentation()
        self.cmake_build('install')


    def maybe_test(self):
        if self.testcling:
            self.cmake_build('cling-test')
        else:
            # create empty xunit file to make Jenkins publisher happy.
            xunitout = open('tools/cling/test/lit-xunit-output.xml', 'wb')
            xunitout.write('<testsuite tests="1" skipped="1"><testcase classname="SKIPPED" name="SKIPPED"><skipped/></testcase></testsuite>')
            xunitout.close()

        if self.testllvmclang:
            self.cmake_build('check-llvm')
            # NO check_call - clang's test suite is known to fail with cling patches!
            self.cmake_build('clang-test', check=False)


    def documentation(self):
        if self.doxygen:
            self.cmake_build('doxygen-cling')
            # make install wants to copy them.
            mkdir_p('tools/clang/docs/doxygen/html')
            mkdir_p('docs/doxygen/html')


    def scp_documentation(self):
        if self.doxygen and self.binaries:
            # Make docs/doxygen/html/html available as doxygen/ for copying to /eos in Jenkins
            if os.path.isdir('doxygen'):
                shutil.rmtree('doxygen')
            shutil.copytree(os.path.join(self.instdir, 'docs', 'html', 'html'), 'doxygen')
            # and then publish to EOS:
            check_call('rsync -avz -e "ssh -K -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" doxygen lxplus:/eos/project/r/root-eos/www/cling/', shell=True)

    def packaging(self):
        if os.path.isdir('artifacts'):
            shutil.rmtree('artifacts') # remove old files, no need to re-copy
        mkdir_p('artifacts') # needed for scp step, even if empty

        if self.binaries:
            if self.doxygen:
                # Grab doc from inst/ then rm -rf it so it doesn't end up in binary.
                os.chdir(os.path.join(self.instdir, 'docs'))
                tar = tarfile.open(os.path.join(self.workspace, 'artifacts', 'cling_' + self.today + '_docs.tar.bz2'), "w:bz2")
                tar.add('html')
                tar.close()
                os.chdir(self.workspace)

            # Tar the install directory.
            tar = tarfile.open(os.path.join('artifacts', self.instdir + '.tar.bz2'), "w:bz2")
            tar.add(self.instdir)
            tar.close()

            if self.label == 'ubuntu22':
                # Tar the source directory.
                shutil.rmtree('src/.git') # remove .git; not part of source tar
                tar = tarfile.open(os.path.join('artifacts', 'cling_' + self.today + '_sources.tar.bz2'), "w:bz2")
                tar.add('src')
                tar.close()

    def housekeeping(self):
        if os.path.isdir(self.instdir):
            shutil.rmtree(self.instdir)

    def build(self):
        print('STEP: CLEAN')
        self.maybe_clean()

        mkdir_p('obj')
        os.chdir('obj')
        print('STEP: CONFIGURE')
        self.configure()
        print('STEP: MAKE')
        self.make()
        print('STEP: TEST')
        self.maybe_test()

        os.chdir(self.workspace)
        print('STEP: COPY DOC TO EOS')
        self.scp_documentation()
        print('STEP: PACKAGING')
        self.packaging()
        print('STEP: HOUSEKEEPING')
        self.housekeeping()


# Create controlfile
open('controlfile','w').write('')

# Remove old build directories:
import shutil, os, time
now = time.time()
for filename in os.listdir(".."):
  if filename.startswith("cling_"):
    fullname = os.path.join("..", filename)
    if os.path.getmtime(fullname) < now - 3 * 86400: #three days
      print("Removing " + fullname)
      shutil.rmtree(fullname)


import os, sys, subprocess

generatorType = 'Unix Makefiles'
label = os.environ['LABEL']
if 'win' in label:
  generatorType = 'Visual Studio 9 2008'

if 'ubuntu22' in label:
  # Get a krb ticket to be able to copy doxygen files to the master's /eos.
  subprocess.check_call("/usr/bin/kinit sftnight@CERN.CH -5 -V -k -t /ec/conf/sftnight.keytab", shell=True)


sys.path.append('./rootspi/jenkins')
import cling_build
bld = cling_build.Builder(
  workspace = os.environ["WORKSPACE"],
  label = label,
  generatorType = generatorType,
  cleanbuild = os.environ['CLEAN'] == 'true',
  binaries = os.environ['BINARIES'] == 'true',
  buildcause = os.environ.get('ROOT_BUILD_CAUSE'),
  testcling = os.environ['TESTCLING'] == 'true',
  testllvmclang = os.environ['TESTLLVMCLANG'] == 'true'
)

bld.build()
