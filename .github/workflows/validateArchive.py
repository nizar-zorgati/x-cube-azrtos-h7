#!/usr/bin/env python3

################################################################################
# Copyright (c) 2020 STMicroelectronics.
# All rights reserved. This program and the accompanying materials
# is the property of STMicroelectronics and must not be
# reproduced, disclosed to any third party, or used in any
# unauthorized manner without written consent.
################################################################################
#
# Author: Torbjörn SVENSSON <torbjorn.svensson@st.com>
#
################################################################################

import enum
from optparse import OptionParser
import os
import pathlib
import pprint
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
import hashlib
import logging
import time

#start run-time of the script 
start_time=time.time()

#configure the logger file
logging.basicConfig(filename="results.log", format='%(message)s',filemode='w')
logger=logging.getLogger()  
logger.setLevel(logging.DEBUG)

#join path
def myPathJoin(*args):
    return "/".join(args).replace("//", "/")

#appaend a slash if it's missing at the end of the path
def appendSlashIfMissing(s):
    if s and s[-1] != "/":
        return s + "/"
    return s

#returns the directory name of s
def getParentDirectory(s):
    # Remove any trailing slashes
    while s and s[-1] == "/":
        s = s[0:-1]

    # Return the parent directoy
    return appendSlashIfMissing(os.path.dirname(s))

#returns the basename of s
def getBasename(s):
    # Remove any trailing slashes
    while s and s[-1] == "/":
        s = s[0:-1]

    return os.path.basename(s)


def toSysmemHash(content):
    # Strip comments and newline
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.S)
    content = re.sub(r'//.*$', '', content)
    content = re.sub(r'\r', '', content)

    return hashlib.md5(content.encode("utf-8")).hexdigest()



McuFamily = enum.Enum("McuFamily", """
        STM32C0
        STM32F0
        STM32F1
        STM32F2
        STM32F3
        STM32F4
        STM32F7
        STM32L0
        STM32L1
        STM32L4
        STM32L5
        STM32H7
        STM32H5
        STM32WB
        STM32WBA
        STM32WL
        STM32G0
        STM32GK
        STM32G4
        STM32MP1
        STM32MP13
        STM32MP2
        STM32U0
        STM32U5
        STM32N6
    """)

def toMcuFamily(mcuName):
    items = sorted(McuFamily.__members__.items(), key=lambda x: len(x[0]), reverse=True)
    for name, member in items:
        if mcuName.startswith(name):
            return member
    raise LookupError()


class Archive(object):
    def __init__(self, path):
        self.__model = dict()
        if os.path.isdir(path):
            if os.name == "nt":
                # It's impossible to support directory scanning on Windows and still detect UPPER/LOWER case issues.
                raise NotImplementedError("Directories are not supported on Windows python. Run the script in Cygwin or use another platform.")

            self.root_path = os.path.abspath(path)
            for x in pathlib.Path(self.root_path).glob("**/*"):
                tmp = str(x)
                # Directories should be denoted with an ending slash
                if os.path.isdir(tmp):
                    tmp = appendSlashIfMissing(tmp)
                # Strip of the "root" part of the tree to check
                tmp = tmp[len(self.root_path):]
                # All paths should be absolute with the root set to the supplied path
                if tmp[0] != "/":
                    tmp = "/" + tmp
                self.__model[tmp] = x
        else:
            self.zf = zipfile.ZipFile(path)
            for x in self.zf.namelist():
                tmp = x

                # All paths should be absolute in the zip file
                if tmp[0] != "/":
                    tmp = "/" + tmp

                self.__model[tmp] = x

                # Add all parent directories
                while tmp != "/":
                    tmp = getParentDirectory(tmp)
                    parent = appendSlashIfMissing(tmp)
                    if parent not in self.__model:
                        self.__model[parent] = None

    #returns the file names
    def getFilenames(self):
        return self.__model.keys()

    #return the file content
    def getFileContent(self, path):
        def convertToStr(x):
            if type(x) == type(""):
                return x
            return x.decode()

        if hasattr(self, "root_path"):
            with open(myPathJoin(self.root_path, path), "rb") as f:
                return convertToStr(f.read())
        else:
            with self.zf.open(self.__model[path]) as f:
                return convertToStr(f.read())

class Main(object):
    def __init__(self, options, archives):
        self.options = options
        self.archives = list(reversed(archives))
        self.names = set()
        for archive in self.archives:
            self.names.update(archive.getFilenames())
        self.lc_names = set(map(lambda x: x.lower(), self.names))

        self.dom_cache = dict()
        self.link_validation_cache = set()

        if options.sysmem_file:
            with open(options.sysmem_file, "r") as f:
                self.SYSMEM_C_HASH = toSysmemHash(f.read())
            self.print("Overriding sysmem.c hash to: {}".format(self.SYSMEM_C_HASH))
        else:
            # This is the hash of the file included in CubeIDE from version 1.4.0
            self.SYSMEM_C_HASH = "f757d275b06e3ed0cab2a4da40a6b131"

    #Customized print function
    def print(self, s):
        if self.options.jenkinsParser:
            msg = s.lstrip()
            indent = s[0:-len(msg)]
            logger.error("{}ERROR_QA: {}".format(indent, msg))
        #excludes the given error codes from the .log report
        else:
            if not options.error_to_exclude :
                logger.error(s)
            else : 
                l = s.split(":")
                codes = options.error_to_exclude.split(",")
                if l[0] in codes : 
                    pass 
                else :
                    logger.error(s)
    

    def list(self, root=None, func=None):
        root = appendSlashIfMissing(root)
        for p in self.names:
            if (root is None or (p.startswith(root) and p != root)) and (func is None or func(p)):
                yield p

    #return the projects present in the archive to validate
    def listProjects(self):
        if self.options.forceCubeIDE:
            return self.listEclipseProjects()

        isProjectDir = lambda x: getBasename(x) in ["EWARM", "MDK-ARM", "STM32CubeIDE", "TrueSTUDIO", "SW4STM32"]
        return map(getParentDirectory, self.list(func=isProjectDir))

    #returns the Eclipse based projects
    def listEclipseProjects(self, root=None):
        isProjectFile = lambda x: getBasename(x) == ".project"
        return map(getParentDirectory, self.list(root=root, func=isProjectFile))

    #returns a list of the scripts present in the archive to validate
    def listScripts(self):
        def isScript(name):
            for suffix in [ ".py", ".sh", ".bat" ]:
                if name.endswith(suffix):
                    return True
            return False
        return self.list(func=isScript)

    #returns the file content
    def getFileContent(self, path):
        if path in self.names:
            for archive in self.archives:
                try:
                    return archive.getFileContent(path)
                except:
                    pass
        return None


    def __getDOM(self, path):
        root = self.dom_cache.get(path)
        if root is None:
            content = self.getFileContent(path)
            if content is not None:
                root = ET.fromstring(content)
                self.dom_cache[path] = root
        return root

    #check if the ide is eclipse based
    def isEclipseBasedIde(self, projRoot):
        if self.options.forceCubeIDE:
            return True

        pattern = re.compile(r"^" + re.escape(appendSlashIfMissing(projRoot)) + r"(STM32CubeIDE|TrueSTUDIO|SW4STM32)(/.+)*/.project$")

        for path in map(getParentDirectory, self.list(root=projRoot, func=lambda x: pattern.match(x))):
            projName = self.getEclipseProjectName(path)
            if projName is not None:
                return True
        return False

    #get the name of the Eclipse project
    def getEclipseProjectName(self, proj):
        root = self.__getDOM(myPathJoin(proj, ".project"))
        if root is not None:
            return root.find('.//name').text
        return None

    #gets the Eclipse nature
    def getEclipseNatures(self, proj):
        root = self.__getDOM(myPathJoin(proj, ".project"))
        if root is not None:
            return set([x.text for x in root.findall('.//natures/nature')])
        return []


    def getEclipseProjectReferences(self, proj):
        root = self.__getDOM(myPathJoin(proj, ".project"))
        if root is not None:
            return set([x.text for x in root.findall('.//projects/project')])
        return None

    #check the cdt nature of the project
    def isCdtProject(self, proj):
        natures = self.getEclipseNatures(proj) or set()
        if natures.intersection(["org.eclipse.cdt.core.cnature", "org.eclipse.cdt.core.ccnature"]):
            return True
        return False

    #returns Mcus for the CubeIDE project
    def getMcusForCubeIDEPRoject(self, proj):
        mcus = set()
        root = self.__getDOM(myPathJoin(proj, ".cproject"))
        if root is not None:
            for option in root.findall(".//storageModule[@moduleId='cdtBuildSystem']/configuration/folderInfo/toolChain/option[@superClass='com.st.stm32cube.ide.mcu.gnu.managedbuild.option.target_mcu']"):
                value = option.attrib.get("value")
                if value:
                    mcus.add(value)
        return mcus

    #Eclipse project validations
    def validateEclipseProject(self, proj):
        valid = True

        if appendSlashIfMissing(proj).endswith("RemoteSystemsTempFiles/"):
            error_code= "ER001"
            self.print("{}: \t Sub-project should be removed!".format(error_code)) 
            return False

        root = self.__getDOM(myPathJoin(proj, ".project"))
        if root is None:
            error_code= "ER002"
            self.print("{}: \t.project file missing in {}".format(error_code,proj))
            return False

        def gettext(node):
            if node is not None:
                return node.text
            return None

        # List all IOC files. They are always supposed to exist in the parent directory.
        parent = getParentDirectory(proj)
        iocFiles = set(self.list(parent, lambda x: "/" not in os.path.relpath(x, parent) and x.endswith(".ioc")))

        # Basic validation of IOC file
        for iocFile in iocFiles:
            content = re.split(r"\r?\n", self.getFileContent(iocFile))
            basename = getBasename(iocFile)
            for line in content:
                if line and not line.startswith("#"):
                    (key, value) = line.split("=", 1)
                    if key == "ProjectManager.ProjectFileName":
                        if value != basename:
                            error_code = "ER003"
                            self.print("{}: \tWarning: {} has wrong filename attribute: {}".format(error_code,basename, value))
                            valid = False
                    elif key == "ProjectManager.ProjectName":
                        if value != basename[0:-4]: # Remove ".ioc"
                            error_code = "ER004"
                            self.print("{}: \tWarning: {} has wrong project name attribute: {}".format(error_code,basename, value))
                            valid = False

        # Validate linked resources
        for link in root.findall('.//linkedResources/link'):
            linkType = gettext(link.find('type'))
            dest = gettext(link.find('name'))
            src = gettext(link.find('locationURI'))

            if src is None:
                # Fallback to old eclipse format
                src = gettext(link.find('location'))

            if src is None and dest is None and linkType is None:
                valid = False
                error_code= "ER008"
                self.print("{}:\tDetected empty link node in  {}".format(error_code,myPathJoin(proj, ".project")))
            elif linkType == "1": # Linked file
                if not self.validateFileLink(proj, src, dest):
                    valid = False

                resolved = self.resolveLink(proj, src)
                iocFiles.discard(resolved)
            elif linkType == "2": # Linked directory
                if not self.validateDirLink(proj, src, dest):
                    valid = False

                if src != "virtual:/virtual":
                    resolved = self.resolveLink(proj, src)
                    iocFiles = set(filter(lambda x: not x.startswith(resolved), iocFiles))
            else:
                error_code = "ER005"
                self.print("{}: \tWarning: link type {} not validated".format(error_code,linkType))

        # Validate that all found IOC files are linked in the project
        if len(iocFiles):
            error_code = "ER006"
            self.print("{} : \tOne or more IOC files detected that are not reference by Eclipse project: {}".format(error_code,sorted(iocFiles)))
            valid = False

        pathSegments = proj.split("/")

        if "SW4STM32" in pathSegments and "STM32CubeIDE" in pathSegments:
            error_code = "ER007"
            self.print("{}: \tInvalid directory tree detected".format(error_code))
            valid = False

        if "SW4STM32" in pathSegments:
            if not self.validateSW4STM32Project(proj):
                valid = False

        if "STM32CubeIDE" in pathSegments or self.options.forceCubeIDE:
            if not self.validateCubeIDEProject(proj):
                valid = False

        return valid

    #SW4STM32 projects validation
    def validateSW4STM32Project(self, proj):
        valid = True

        projectName = self.getEclipseProjectName(proj)
        natures = self.getEclipseNatures(proj)

        if "org.eclipse.cdt.core.cnature" not in natures:
            error_code = "ER009"
            self.print("{}: \tExpected C nature".format(error_code))
            valid = False

        root = self.__getDOM(myPathJoin(proj, ".cproject"))
        if root is None:
            error_code = "ER010"
            self.print("{}: \t.cproject file missing in {}".format(error_code,proj))
            return False

        # Validate sysmem.c files
        if not self.validateSysmemC(proj):
            valid = False

        for config in root.findall(".//storageModule[@moduleId='cdtBuildSystem']/configuration"):
            configName = config.attrib.get("name")
            build_path = myPathJoin(proj, configName or "dummy")

            headerPrinted = [False] # Need list to trick python to not treat as local variable in buildConfPrint() function
            def buildConfPrint(s):
                if not headerPrinted[0]:
                    headerPrinted[0] = True
                    logger.debug("\tValidating build configuration {}".format(configName))
                self.print(s)

            # Ensure configuration is for SW4STM32
            configParent = config.attrib.get("parent")
            if not configParent.startswith("fr.ac6.managedbuild."):
                error_code = "ER011"
                buildConfPrint("{}: Unexpected build configuration with parent = {}".format(error_code,configParent))
                valid = False


        for config in root.findall('.//configuration'):
            build_path = myPathJoin(proj, config.attrib.get("name") or "dummy")
            for option in config.findall('.//option'):
                superClass = option.attrib.get("superClass")
                if superClass == "gnu.both.asm.option.include.paths":
                    if not self.__validateIncludePathsOption(proj, build_path, option, "ASM", buildConfPrint):
                        valid = False
                elif superClass == "gnu.c.compiler.option.include.paths":
                    if not self.__validateIncludePathsOption(proj, build_path, option, "C", buildConfPrint):
                        valid = False
                elif superClass == "gnu.cpp.compiler.option.include.paths":
                    if not self.__validateIncludePathsOption(proj, build_path, option, "CPP", buildConfPrint):
                        valid = False
                elif superClass == "fr.ac6.managedbuild.tool.gnu.cross.c.linker.script":
                    if not self.__validateLinkerScriptOption(proj, build_path, option, "C", buildConfPrint):
                        valid = False
                elif superClass == "fr.ac6.managedbuild.tool.gnu.cross.cpp.linker":
                    if not self.__validateLinkerScriptOption(proj, build_path, option, "CPP", buildConfPrint):
                        valid = False

        return valid

    #CubeIDE project validation
    def validateCubeIDEProject(self, proj):
        valid = True

        projectName = self.getEclipseProjectName(proj)
        natures = self.getEclipseNatures(proj)

        mcus = self.getMcusForCubeIDEPRoject(proj)
        if not mcus:
            # Assume children have specified target MCU
            for child in self.list(proj, lambda x: x and x[-1] == "/"):
                mcus.update(self.getMcusForCubeIDEPRoject(child))

        if not mcus:
            error_code= "ER012"
            self.print("{}: \tUnable to determine MCU for project".format(error_code))
            valid = False

        mcuFamily = toMcuFamily(next(iter(mcus)))

        if len(mcus) != 1:
            error_code= "ER013"
            self.print("{}: \tMore than one MCU defined. Only basic validation will be performed!: {}".format(error_code,mcus))
            valid = False
            mcuFamily = None

        if "com.st.stm32cube.ide.mcu.MCURootProjectNature" in natures:
            # Special validation for L5/U5
            if mcuFamily in (McuFamily.STM32L5, McuFamily.STM32U5):
                if "com.st.stm32cube.ide.mcu.MCUEndUserDisabledTrustZoneProjectNature" not in natures:
                    # Check existence of sub-projects
                    hasNonSecureChild = False
                    hasSecureChild = False
                    for child in self.list(proj, lambda x: x and x[-1] == "/"):
                        childNatures = self.getEclipseNatures(child)
                        if childNatures is None:
                            # Not an Eclipse project
                            continue
                        if "com.st.stm32cube.ide.mcu.MCUSecureProjectNature" in childNatures:
                            hasSecureChild = True
                        if "com.st.stm32cube.ide.mcu.MCUNonSecureProjectNature" in childNatures:
                            hasNonSecureChild = True

                    if not hasSecureChild:
                        error_code = "ER014"
                        self.print("{}: \tSecure project missing : {} ".format(error_code,proj))
                        valid = False

                    if not hasNonSecureChild:
                        error_code = "ER015"
                        self.print("{}: \tNon-secure project missing : {}".format(error_code,proj))
                        valid = False

            # Root project names should always match the directory or the directory should be named "STM32CubeIDE"
            dirname = getBasename(proj)
            if dirname != "STM32CubeIDE" and dirname != projectName:
                error_code = "ER016"
                self.print("{}: \tProject name \"{}\" does not match directory name \"{}\".".format(error_code,projectName, dirname))
                valid = False

            if myPathJoin(proj, ".cproject") not in self.names:
                # Hierical project should never have C or C++ natures
                for nature in natures.intersection(["org.eclipse.cdt.core.cnature", "org.eclipse.cdt.core.ccnature"]):
                    error_code = "ER017"
                    self.print("{}: \tNature \"{}\" not expected for hierical projects : {} ".format(error_code,nature,proj))
                    valid = False

                #Complex project validation ends here
                return valid


        # Project is expected to be a C or C++ project

        if "org.eclipse.cdt.core.cnature" not in natures:
            error_code = "ER018"
            self.print("{}: \tExpected C nature : {}".format(error_code,proj))
            valid = False

        # Validate sysmem.c files
        if not self.validateSysmemC(proj):
            valid = False

        root = self.__getDOM(myPathJoin(proj, ".cproject"))
        if root is None:
            error_code = "ER011"
            self.print("{}: \t.cproject file missing in {}".format(error_code,proj))
            return False

        # Handle special non-build config validation here
        if mcuFamily == McuFamily.STM32H7:
            # Special H7 sub-project tests here
            if getBasename(proj) != "STM32CubeIDE":
                if "com.st.stm32cube.ide.mcu.MCUMultiCpuProjectNature" not in natures:
                    error_code = "ER019"
                    self.print("{}: \tMissing nature com.st.stm32cube.ide.mcu.MCUMultiCpuProjectNature : {}".format(error_code,proj))
                    valid = False

                # Validate name of sub-project
                parent = getParentDirectory(proj)
                parentNatures = self.getEclipseNatures(parent)
                if "com.st.stm32cube.ide.mcu.MCURootProjectNature" in parentNatures:
                    parentProjectName = self.getEclipseProjectName(parent)
                    dirname = getBasename(proj)
                    if projectName != "_".join([parentProjectName, dirname]):
                        error_code = "ER020"
                        self.print("{}: \tProject name does not match expected name : {} ".format(error_code,proj))
                        valid = False

        elif mcuFamily in (McuFamily.STM32L5, McuFamily.STM32U5):
            # Special L5/U5 sub-project tests here

            if "com.st.stm32cube.ide.mcu.MCUSecureProjectNature" in natures and "com.st.stm32cube.ide.mcu.MCUNonSecureProjectNature" in natures:
                error_code = "ER021"
                self.print("{}: Same project cannot be tagged both secure and non-secure : {} ".format(error_code,proj))
                valid = False

            if "com.st.stm32cube.ide.mcu.MCUEndUserDisabledTrustZoneProjectNature" not in natures:
                # Validate name of sub-project
                parent = getParentDirectory(proj)
                parentNatures = self.getEclipseNatures(parent)
                parentProjectName = self.getEclipseProjectName(parent)
                if "com.st.stm32cube.ide.mcu.MCURootProjectNature" in parentNatures:
                    if "com.st.stm32cube.ide.mcu.MCUSecureProjectNature" in natures:
                        if projectName != parentProjectName + "_Secure":
                            error_code = "ER022"
                            self.print("{}: \tProject name does not match expected secure name : {}".format(error_code,proj))
                            valid = False
                    if "com.st.stm32cube.ide.mcu.MCUNonSecureProjectNature" in natures:
                        if projectName != parentProjectName + "_NonSecure":
                            error_code = "ER023"
                            self.print("{}: \tProject name does not match expected non-secure name : {}".format(error_code,proj))
                            valid = False

                # Validate project reference
                if "com.st.stm32cube.ide.mcu.MCUNonSecureProjectNature" in natures:
                    projectRefs = self.getEclipseProjectReferences(proj)
                    secureProjectName = parentProjectName + "_Secure"
                    if secureProjectName not in projectRefs:
                        error_code = "ER024"
                        self.print("{}: \tMissing project reference to {}".format(error_code,secureProjectName))
                        valid = False


        configNames = [x.attrib.get("name") for x in root.findall(".//storageModule[@moduleId='cdtBuildSystem']/configuration")]

        # All projects is expected to have a build configuration named "Debug"
        if "Debug" not in configNames:
            error_code = "ER025"
            self.print("{}:  \tExpecting build configuration named \"Debug\" : {} ".format(error_code,proj))
            valid = False

        # All projects is expected to have a build configuration named "Release"
        if "Release" not in configNames:
            error_code = "ER026"
            self.print("{}: \tExpecting build configuration named \"Release\" : {} ".format(error_code,proj))
            valid = False

        # Validate order of build configurations
        if set(configNames) == set(["Debug", "Release"]):
            if ["Debug", "Release"] != configNames:
                error_code = "ER027"
                self.print("{}: \tWrong order of build configurations: {} : {}".format(error_code,configNames,proj))
                valid = False

        C_OPTIMIZATION_DEBUG = ["com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.compiler.option.optimization.level.value.o0", "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.compiler.option.optimization.level.value.og"]
        C_OPTIMIZATION_RELEASE = ["com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.compiler.option.optimization.level.value.os"]
        CPP_OPTIMIZATION_DEBUG = ["com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.compiler.option.optimization.level.value.o0", "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.compiler.option.optimization.level.value.og"]
        CPP_OPTIMIZATION_RELEASE = ["com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.compiler.option.optimization.level.value.os"]

        for config in root.findall(".//storageModule[@moduleId='cdtBuildSystem']/configuration"):
            configName = config.attrib.get("name")
            build_path = myPathJoin(proj, configName or "dummy")

            headerPrinted = [False] # Need list to trick python to not treat as local variable in buildConfPrint() function
            def buildConfPrint(s):
                if not headerPrinted[0]:
                    headerPrinted[0] = True
                    if options.debug_option :
                        logger.debug("\tValidating build configuration {}".format(configName))
                self.print(s)

            # Ensure configuration is for STM32CubeIDE
            configParent = config.attrib.get("parent")
            if not configParent.startswith("com.st.stm32cube.ide."):
                error_code = "ER011"
                buildConfPrint("{}: Unexpected build configuration with parent = {} : {}".format(error_code,configParent,proj))
                valid = False

            # Validate toolchain selection
            def getToolchainOption(name):
                option = config.find(".//option[@superClass='{}']".format(name))
                if option is not None:
                    return option.attrib.get("value")
                return None
            toolchainId = getToolchainOption("com.st.stm32cube.ide.mcu.gnu.managedbuild.option.toolchain")
            if toolchainId is None:
                # Legacy support
                tcDefault = getToolchainOption("com.st.stm32cube.ide.mcu.option.internal.toolchain.default")
                if "false" == tcDefault:
                    tcType = getToolchainOption("com.st.stm32cube.ide.mcu.option.internal.toolchain.type")
                    tcVersion = getToolchainOption("com.st.stm32cube.ide.mcu.option.internal.toolchain.version")
                    error_code = "ER034"
                    buildConfPrint("{}: Fixed toolchain detected ({}, {}), but should not be set!".format(error_code,tcType, tcVersion))
                    valid = False
            elif toolchainId != "com.st.stm32cube.ide.mcu.gnu.managedbuild.option.toolchain.value.workspace":
                error_code = "ER034"
                buildConfPrint("{}: Fixed toolchain detected ({}), but should not be set! : {}".format(error_code,toolchainId,proj))
                valid = False

            for option in config.findall('.//option'):
                superClass = option.attrib.get("superClass")
                if superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.compiler.option.optimization.level":
                    value = option.attrib.get("value")
                    if value is None:
                        if options.pedantic:
                            error_code = "ER027"
                            buildConfPrint("{}: No C optimization level defined : {}".format(error_code,proj))
                            valid = False
                    elif (configName == "Debug" and value not in C_OPTIMIZATION_DEBUG) or (configName == "Release" and value not in C_OPTIMIZATION_RELEASE):
                        error_code = "ER028"
                        buildConfPrint("{}: Wrong C optimization level: {} : {}".format(error_code,value,proj))
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.compiler.option.optimization.level":
                    value = option.attrib.get("value")
                    if value is None:
                        if options.pedantic:
                            error_code = "ER027"
                            buildConfPrint("{}: No CPP optimization level defined : {}".format(error_code,proj))
                            valid = False
                    elif (configName == "Debug" and value not in CPP_OPTIMIZATION_DEBUG) or (configName == "Release" and value not in CPP_OPTIMIZATION_RELEASE):
                        error_code = "ER028"
                        buildConfPrint("{}: Wrong CPP optimization level: {} : {}".format(error_code,value,proj))
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.compiler.option.debuglevel":
                    value = option.attrib.get("value")
                    if value is None:
                        if options.pedantic:
                            error_code = "ER029"
                            buildConfPrint("{}: No C debug level defined : {} ".format(error_code,proj))
                            valid = False
                    elif (configName == "Debug" and value != "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.compiler.option.debuglevel.value.g3") or \
                         (configName == "Release" and value != "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.compiler.option.debuglevel.value.g0"):
                        error_code = "ER030"
                        buildConfPrint("{}: Wrong C debug level: {} : {}".format(error_code,value,proj))
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.compiler.option.debuglevel":
                    value = option.attrib.get("value")
                    if value is None:
                        if options.pedantic:
                            error_code = "ER029"
                            buildConfPrint("{}: No C debug level defined : {}".format(error_code,proj))
                            valid = False
                    elif (configName == "Debug" and value != "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.compiler.option.debuglevel.value.g3") or \
                         (configName == "Release" and value != "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.compiler.option.debuglevel.value.g0"):
                        error_code = "ER030"
                        buildConfPrint("{}: Wrong CPP debug level: {} : {}".format(error_code,value,proj))
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.assembler.option.includepaths":
                    if not self.__validateIncludePathsOption(proj, build_path, option, "ASM", buildConfPrint):
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.compiler.option.includepaths":
                    if not self.__validateIncludePathsOption(proj, build_path, option, "C", buildConfPrint):
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.compiler.option.includepaths":
                    if not self.__validateIncludePathsOption(proj, build_path, option, "CPP", buildConfPrint):
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.linker.option.script":
                    if not self.__validateLinkerScriptOption(proj, build_path, option, "C", buildConfPrint):
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.linker.option.script":
                    if not self.__validateLinkerScriptOption(proj, build_path, option, "CPP", buildConfPrint):
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.linker.option.additionalobjs":
                    if any(x.attrib.get("value") == "" for x in option.findall("./listOptionValue")):
                        error_code = "ER031"
                        buildConfPrint("{}: C linker \"\" is not a valid additional object".format(error_code))
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.linker.option.additionalobjs":
                    if any(x.attrib.get("value") == "" for x in option.findall("./listOptionValue")):
                        error_code = "ER030"
                        buildConfPrint("{}: CPP linker \"\" is not a valid additional object : {}".format(error_code,proj))
                        valid = False


                # Special U5/L5 handling
                if mcuFamily in (McuFamily.STM32L5, McuFamily.STM32U5):
                    if "com.st.stm32cube.ide.mcu.MCUEndUserDisabledTrustZoneProjectNature" not in natures:
                        if "com.st.stm32cube.ide.mcu.MCUNonSecureProjectNature" in natures:
                            expectedLib = "${workspace_loc:/%s/%s/secure_nsclib.o}" % (projectName.replace("_NonSecure", "_Secure"), configName)
                            if superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.linker.option.additionalobjs":
                                values = [x.attrib.get("value") for x in option.findall("./listOptionValue")]
                                if expectedLib not in values:
                                    error_code = "ER032"
                                    buildConfPrint("{}: Missing secure_nsclib.o on C linker: {} : {}".format(error_code,expectedLib,proj))
                                    valid = False
                            elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.linker.option.additionalobjs":
                                values = [x.attrib.get("value") for x in option.findall("./listOptionValue")]
                                if expectedLib not in values:
                                    error_code = "ER032"
                                    buildConfPrint("{}: Missing secure_nsclib.o on CPP linker: {} : {}".format(error_code,expectedLib,proj))
                                    valid = False

            for inputType in config.findall('.//inputType'):
                superClass = inputType.attrib.get("superClass")
                if superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c.linker.input":
                    additionalInputPaths = [x.attrib.get("paths") for x in inputType.findall("./additionalInput")]
                    if "$(LIBS)" not in additionalInputPaths:
                        error_code = "ER033"
                        buildConfPrint("{}: Missing $(LIBS) on C linker : {} ".format(error_code,proj))
                        valid = False
                elif superClass == "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.cpp.linker.input":
                    additionalInputPaths = [x.attrib.get("paths") for x in inputType.findall("./additionalInput")]
                    if "$(LIBS)" not in additionalInputPaths:
                        error_code = "ER033"
                        buildConfPrint("{}: Missing $(LIBS) on CPP linker : {}".format(error_code,proj))
                        valid = False

        return valid

    #SysmemC validation
    def validateSysmemC(self, proj):
        valid = True

        # Validate sysmem.c
        for sysmemFile in self.list(proj, lambda x: os.path.basename(x).lower() == "sysmem.c"):
            content = self.getFileContent(sysmemFile)

            if self.SYSMEM_C_HASH != toSysmemHash(content):
                error_code = "ER035"
                self.print("{} : \tUnexpected sysmem.c content located at {}".format(error_code,sysmemFile))
                valid = False

        return valid

    #Script validations
    def validateScript(self, script):
        valid = True
        content = self.getFileContent(script)

        if content == "":
            # Empty files are always considered valid
            return True

        eolFormat = None
        newlines = "".join(re.split(r"[^\r\n]+", content))
        #get the EOL format
        if re.match(r"^(\r\n)*$", newlines):
            eolFormat = "CRLF"
        elif "\n" in newlines and "\r" not in newlines:
            eolFormat = "LF"
        elif "\r" in newlines and "\n" not in newlines:
            eolFormat = "CR"
        else:
            eolFormat = "MIX"

        line0 = re.split(r"(\r\n|\r|\n)", content, 1)[0] #Shebang
        interpreter = None
        m = re.match(r"#!\s*(\S+)\s*(.*)$", line0)
        if m:
            interpreter = getBasename(m.group(1))
            optionalArgs = m.group(2).split(" ")
            if interpreter == "env":
                # Using env to locate the interpreter 
                if len(optionalArgs) == 0:
                    interpreter = None
                    valid = False
                    error_code = "ER036"
                    self.print("{}: \tUnable to identify interpreter : {} ".format(error_code,script))
                else:
                    interpreter = optionalArgs[0]
                    optionalArgs = optionalArgs[1:]
                    if len(optionalArgs) != 0:
                        valid = False
                        error_code =  "ER037"
                        self.print("{}: \tToo many arguments to env: {}   : {}".format(error_code,optionalArgs,script))
            else:
                if len(optionalArgs) > 0:
                    if optionalArgs[0] == "-":
                        optionalArgs = optionalArgs[1:]

                if len(optionalArgs) == 1 and optionalArgs[0] == "":
                    optionalArgs = []

                if len(optionalArgs) > 0:
                    valid = False
                    error_code = "ER038"
                    self.print("{}: \tOptional arguments might not be used : {} ".format(error_code,script))

        else:
            #get the interpreter of the script
            scriptExt = os.path.splitext(script)[1]
            if scriptExt == ".sh":
                interpreter = "bash"
            elif scriptExt == ".py":
                interpreter = "python"
            elif scriptExt == ".bat":
                interpreter = "bat"

        if interpreter in ("bash", "sh", "python", "python2", "python3"):
            # Validate that script has UNIX EOL
            if eolFormat != "LF":
                valid = False
                if eolFormat == "CRLF":
                    error_code = "ER039"
                    self.print("{}: \tDOS line endings : {} .".format(error_code,script))
                elif eolFormat == "MIX":
                    error_code = "ER040"
                    self.print("{}: \tMixed line endings detected : {} ".format(error_code,script))
                if line0.startswith("#!"): 
                    error_code = "ER041"
                    self.print("{}: \tShebang unreliable! Script might not execute properly for end-user : {} ".format(error_code,script))


        elif interpreter == "bat":
            # Valdiate that script has DOS EOL
            if eolFormat != "CRLF":
                valid = False
                if eolFormat == "LF":
                    error_code = "ER042"
                    self.print("{}: \tUNIX line endings : {} ".format(error_code,script))
                elif eolFormat == "MIX":
                    error_code = "ER043"
                    self.print("{}: \tMixed line endings detected : {} ".format(error_code,script))

        else:
            valid = False
            error_code = "ER044"
            self.print("{}: \tInterpreter {} has not been validated : {}".format(error_code,interpreter,script))

        return valid

    def __resolve(self, proj, build_path, value):
        m = re.match(r"\"?\${workspace_loc:/\${ProjName}/(.*)}(.*?)\"?", value)
        if m:
            return self.resolveRelative(proj, m.group(1) + m.group(2))
        elif not re.match(r"\"?\${(workspace_loc:|ProjName)", value):
            return self.resolveRelative(build_path, self.removeQuotation(value))
        return None

    #Include paths options validation
    def __validateIncludePathsOption(self, proj, build_path, option, tool, reportFunc):
        valid = True

        for listOptionValue in option:
            value = listOptionValue.attrib.get("value")
            if value == "":
                valid = False
                error_code = "ER045"
                reportFunc("{}: \t {} include path \"\" should not be listed : {}".format(error_code,tool))
                continue

            resolved = self.__resolve(proj, build_path, value)
            if resolved is None:
                valid = False
                error_code = "ER046"
                reportFunc("{}: \t {} include path {} cannot be verified : {}".format(error_code,tool, value))
                continue

            if resolved[-1] != "/":
                resolved = resolved + "/"
            if resolved not in self.names:
                valid = False
                if resolved.lower() in self.lc_names:
                    error_code = "ER047"
                    reportFunc("{}: \t {} include path {} has wrong case in archive".format(error_code,tool, resolved))
                else:
                    error_code = "ER048"
                    reportFunc("{}:  \t {} include path {} missing from archive".format(error_code,tool, resolved))

        return valid

    #Linker script options validation
    def __validateLinkerScriptOption(self, proj, build_path, option, tool, reportFunc):
        valid = True

        value = option.attrib.get("value")
        resolved = self.__resolve(proj, build_path, value)
        if resolved is None:
            valid = False
            error_code = "ER049"
            reportFunc("{}: \t {} linker script {} cannot be verified : {}".format(error_code,tool, value))
        elif resolved not in self.names:
            valid = False
            if resolved.lower() in self.lc_names:
                error_code = "ER050"
                reportFunc("{}: \t {} linker script {} has wrong case in archive".format(error_code,tool, resolved))
            else:
                error_code = "ER051"
                reportFunc("{}: \t {} linker script {} missing from archive".format(error_code,tool, resolved))

        return valid

    #removes quotation
    def removeQuotation(self, s):
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            return s[1:-1]
        return s

    def resolveRelative(self, proj, path):
        res = proj
        for p in path.split("/"):
            if p == ".." and not (res == "/" or re.match(r"(/\.\.)+$", res)):
                res = getParentDirectory(res)
            elif p != ".":
                res = myPathJoin(res, p)
        return res

    #File link validation
    def validateFileLink(self, proj, src, dest):
        valid = True

        # Validate that source exist
        resolved = self.resolveLink(proj, src)
        if resolved not in self.names:
            valid = False
            if resolved.lower() in self.lc_names:
                error_code = "ER049"
                self.print("{}: \t{} has wrong case in archive".format(error_code,resolved))
            else:
                error_code = "ER050"
                self.print("{}: \t{} missing from archive".format(error_code,resolved))

        # Validate destination does not hide other resources (with wrong case)
        p = myPathJoin(proj, dest)
        while True:
            # Use cached result if available
            if p in self.link_validation_cache:
                break

            if p.lower() in self.lc_names:
                if p not in self.names:
                    valid = False
                    error_code = "ER051"
                    self.print("{}: \t{} hides other resources".format(error_code,p))
                    break

            # Stop when root is reached
            if not p or p == "/":
                break

            # Cache the validation result
            self.link_validation_cache.add(p)
            p = getParentDirectory(p)

        return valid

    #Directory link validation
    def validateDirLink(self, proj, src, dest):
        valid = True

        # Validate that source exist
        if src != "virtual:/virtual":
            resolved = appendSlashIfMissing(self.resolveLink(proj, src))
            if resolved not in self.names:
                valid = False
                if resolved.lower() in self.lc_names:
                    error_code = "ER049"
                    self.print("{}: \t{} has wrong case in archive".format(error_code,resolved))
                else:
                    error_code = "ER050"
                    self.print("{}: \t{} missing from archive".format(error_code,resolved))

        # Validate destination does not hide other resources (with wrong case)
        p = appendSlashIfMissing(myPathJoin(proj, dest))
        while True:
            # Use cached result if available
            if p in self.link_validation_cache:
                break

            if p.lower() in self.lc_names:
                if p not in self.names:
                    valid = False
                    error_code = "ER051"
                    self.print("{}: \t{} hides other resources".format(error_code,p))
                    break

            # Stop when root is reached
            if not p or p == "/":
                break

            # Cache the validation result
            self.link_validation_cache.add(p)

            p = getParentDirectory(p)

        return valid

    def resolveLink(self, proj, eclipseUri):
        m = re.match(r'(?:\$%7B)?PARENT-([0-9]+)-PROJECT_LOC(?:%7D)?/(.*)', eclipseUri)
        if m:
            d = proj
            for _ in range(int(m.group(1))):
                d = getParentDirectory(d)
            return myPathJoin(d, m.group(2))
        return eclipseUri

    
    def skipProject(self,projlist,skip_reg_files) :
            skip_reg_files =options.file_to_skip.split(",")
            for skip_reg_file in skip_reg_files :
                for proj in projlist :
                    if (re.match(skip_reg_file,proj)) != None :
                        projlist = [x for x in projlist if x!=proj]
            return projlist

    def skipScript(self,scriptlist,scripts_reg_to_skip) :
            scripts_reg_to_skip =options.file_to_skip.split(",")
            indexList = []
            for script_index in range(0,len(scriptlist)):
                for script_to_skip in scripts_reg_to_skip :
                    if (re.match(script_to_skip,scriptlist[script_index])) != None:
                        indexList.append(script_index)
            for index in reversed(indexList) :
                del scriptlist[index]
            return scriptlist       

if __name__ == "__main__":

    #parsing command line tools
    #program version 3.0
    parser = OptionParser("%prog [options] <zipfile or path> [zipfile or path...]",
        description="Later file or path overrides earlier on the command line.",
        version="%prog 3.0") 
    #pedantic option
    parser.add_option("--pedantic", dest="pedantic",
        action="store_true",
        help="Pedantic warnings",
        default=False) 
    #Force cube ide (STM32CubeIDE projects will be valid)
    parser.add_option("--force-cubeide", dest="forceCubeIDE",
        action="store_true",
        help="Force CubeIDE testing",
        default=False) 
    #Symem file check
    parser.add_option("--sysmem-file", dest="sysmem_file",
        help="Use SYSMEM_FILE for comparing",
        default=None) 
    #Parser for jenkins, modifies the display of the output messages
    parser.add_option("--enable-jenkins-parser", dest="jenkinsParser",
        action="store_true",
        help="Prefix output for Jenkins parser",
        default=False) 
    #Each error has a code attached to it, the exclude option will let you eliminate the entered errors from the .log report (seperated by ',')
    #Convinient solution to eliminate false positive errors
    parser.add_option("--exclude", dest="error_to_exclude",type='string',
        help="excludes errors with the matching code") 
    #Skips projects with a given path (seperated by ',')
    parser.add_option("--skip", dest="file_to_skip",type='string',
        help="Excludes Project files/Folders, Scripts -depending from the entered expression- from the validations process. You should enter a Regular Expression (eg .*Filex.*) ")
    #displays the debugging messaging in addition of the error messages
    parser.add_option("--debug", dest="debug_option",action="store_true",help="display the good validated path projects as well as the projects with errors", default=False)
    #the archive to validate 
    parser.add_option("-f",dest="file",type="string",help="you need to specify an archive to validate")

    (options, args) = parser.parse_args()
    archives_to_check = options.file 
    argument = archives_to_check.split(" ")
    #checks if the archive is entered or not
    if len(argument) < 1:
        parser.error("You need to specify at least one archive to validate")
    
    #argument is the archive to validate
    archives = [Archive(path) for path in argument]
    main = Main(options, archives) 

    if options.debug_option and not options.jenkinsParser:
        logger.debug("__ PROJECTS WITH ERRORS AND GOOD VALIDATED PROJECTS ARE PRINTED __")
    elif not options.jenkinsParser :
        logger.debug("__ ONLY PROJECTS WITH ERRORS ARE PRINTED __ ")
    logger.debug("__ Launching {}".format(parser.get_version()))
    logger.debug("__ Command line arguments: {}  ".format(sys.argv))

    #these variables are needed for the summary
    failed_proj = 0
    failed_scripts = 0
    total_projects =0
    failed_subproj = 0 
    total_scripts = 0
    total_subproj =0
    listof_failed_projects = []
    listof_failed_scripts = []
    logger.debug("\n__ PROJECTS VALIDATION __:")

    #check if there are projects to skip
    if options.file_to_skip :
        proj_list = sorted(list(set(main.listProjects())))
        proj_list= main.skipProject(proj_list,options.file_to_skip)
    else :
        proj_list = sorted(list(set(main.listProjects())))  
    #validating Eclipse project
    for proj in proj_list:
        total_projects+=1
        if main.isEclipseBasedIde(proj) and options.debug_option:
            logger.debug("\t valid {} project ".format(getBasename(proj)))
        elif not main.isEclipseBasedIde(proj):
            error_code = "ER100"
            main.print("{}: \tMissing {} STM32CubeIDE project".format(error_code,proj))
            failed_proj+=1
            listof_failed_projects.append(proj)
            continue

        list_subproj = sorted(list(set(main.listEclipseProjects(proj))))
        for subproj in list_subproj:
            if not main.validateEclipseProject(subproj):
                failed_subproj+=1
            if main.validateEclipseProject(subproj) and options.debug_option:
                logger.debug("\t valid {} Eclipse project ".format(getBasename(subproj)))

    logger.debug("\n__ SCRIPTS VALIDATION __:") 
    #checks if there are scripts to skip
    if options.file_to_skip :
        list_scripts = sorted(list(set(main.listScripts())))
        main.skipScript(list_scripts,options.file_to_skip)
    else :
        list_scripts = sorted(list(set(main.listScripts())))

    #validating Scripts
    for script in list_scripts :
        total_scripts+=1
        if not main.validateScript(script):
            failed_scripts+=1
            listof_failed_scripts.append(script)
        else :
            if options.debug_option : 
                logger.debug("valid script {}".format(script))
            
    #Short Summary 
    logger.debug("\n**  SUMMARY: ")
    if ((len(listof_failed_scripts)!= 0) or (len(listof_failed_projects) != 0)):
        main.print("* ERROR_QA: FAILED VALIDATION") 
    else : 
        main.print("* SUCCESSFUL VALIDATION")
    main.print("* The script's run-time : %s seconds " %round((time.time()- start_time),3))
    main.print("* Number of failed projects : {} out of {} ".format(failed_proj, total_projects))
    main.print("* Number of failed scripts : {} out of {} ".format(failed_scripts,total_scripts))

    #printing the content of the .log report
    with open ("results.log","r") as file :
        lines = file.readlines()
        for line in lines :
            print(line)
    file.close()
    # Detailled Summary
    if failed_proj != 0 :
        logger.debug("\n__\t\t\t LIST OF FAILED PROJECTS __ ")
        for i in range(0,failed_proj):
            logger.debug(listof_failed_projects[i])

    if failed_scripts != 0 : 
        logger.debug("\n__\t\t\t LIST OF FAILED SCRIPTS __ ")
        for i in range(0,failed_scripts):
            logger.debug(listof_failed_scripts[i])

    #A simple html report is generated from the results log file when the script is ran
    file = open("results.html","w") 
    with open ("results.log","r") as reportFile:
        lines = reportFile.readlines()
        file.write("<html>")
        file.write("<!DOCTYPE html>")
        file.write("<title> Validate Archive log </title>")
        for line in lines : 
            if line.startswith("__ ONLY"):
                file.write("<p style='color:Blue;'> <b> {} </b> </p>".format(line))
            elif line.startswith("**"):
                file.write("<p style='color:Blue;'> <b> {} </b> </p>".format(line))
            elif line.startswith("*"):
                liner = line.split(":")
                if line.startswith("* ERROR_QA:"):
                    file.write("<p> <b style='color:Red;'> {}: </b> {} </p>".format(liner[0],liner[1]))
                else :
                    file.write("<p> <b style='color:Green;'> {}: </b> {} </p>".format(liner[0],liner[1]))
        for line in lines :
            if line.startswith("__") and not line.startswith("__ ONLY") and not(line.startswith("**") or line.startswith("*")):
                file.write("<p style='color:Blue;'> <b> {} </b> </p>".format(line))
            elif not line.startswith("-") and not line.startswith("__ ONLY") and not(line.startswith("**") or line.startswith("*")):
                file.write("<p> {} </p>".format(line))
            elif line.startswith("**") or line.startswith("*"):
                pass

        file.write("</html>")
    reportFile.close()
    file.close()
                
