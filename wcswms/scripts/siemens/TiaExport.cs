// Offline-only engineering helper. It contains no online/download operations.
using System;
using System.Collections.Generic;
using System.IO;
using System.Xml.Linq;
using Siemens.Engineering;
using Siemens.Engineering.HW;
using Siemens.Engineering.HW.Features;
using Siemens.Engineering.SW;
using Siemens.Engineering.SW.Blocks;
using Siemens.Engineering.SW.Tags;
using Siemens.Engineering.SW.Types;
using Siemens.Engineering.SW.TechnologicalObjects;
using Siemens.Engineering.Compiler;

public static class WarehouseTiaExport {
    static XElement report;
    static string output;
    static HashSet<string> exported;
    static int sequence;
    static bool compileEnabled;
    public static string CompileBlockName;
    static string Safe(string value) {
        foreach (char c in Path.GetInvalidFileNameChars()) value=value.Replace(c,'_');
        return value;
    }
    static XElement Failure(string operation, Exception error) {
        return new XElement("Error",new XAttribute("operation",operation),error.ToString());
    }
    static XElement Message(CompilerResultMessage message) {
        XElement node=new XElement("Message",new XAttribute("state",message.State),new XAttribute("path",message.Path ?? ""),new XElement("Description",message.Description));
        foreach (CompilerResultMessage nested in message.Messages) node.Add(Message(nested));
        return node;
    }
    static void Blocks(PlcBlockGroup group, string prefix, XElement plc) {
        foreach (PlcBlock block in group.Blocks) {
            string blockName=block.Name;
            if(blockName == CompileBlockName) {
                Console.WriteLine("COMPILE BLOCK "+blockName);
                try {
                    var compiler=((IEngineeringServiceProvider)block).GetService<ICompilable>();
                    if(compiler==null) throw new InvalidOperationException("Block compile service unavailable");
                    var result=compiler.Compile();
                    var node=new XElement("Compile",new XAttribute("block",blockName),new XAttribute("state",result.State),new XAttribute("errors",result.ErrorCount),new XAttribute("warnings",result.WarningCount));
                    foreach(var message in result.Messages) node.Add(Message(message));
                    plc.Add(node);
                } catch(Exception error){plc.Add(Failure("compile block "+blockName,error));new XDocument(new XElement(report)).Save(Path.Combine(output,"engineering-progress.xml"));}
            }
            string path=Path.Combine(output,prefix+"-block-"+(++sequence)+"-"+Safe(block.Name)+".xml");
            try {block.Export(new FileInfo(path),ExportOptions.WithDefaults);plc.Add(new XElement("Block",new XAttribute("name",block.Name),new XAttribute("file",Path.GetFileName(path))));}
            catch(Exception error){plc.Add(Failure("export block "+block.Name,error));}
        }
        foreach(PlcBlockUserGroup nested in group.Groups) Blocks(nested,prefix,plc);
    }
    static void Tags(PlcTagTableGroup group,string prefix,XElement plc) {
        foreach(PlcTagTable table in group.TagTables) {
            string path=Path.Combine(output,prefix+"-tags-"+(++sequence)+"-"+Safe(table.Name)+".xml");
            try {table.Export(new FileInfo(path),ExportOptions.WithDefaults);plc.Add(new XElement("TagTable",new XAttribute("name",table.Name),new XAttribute("file",Path.GetFileName(path))));}
            catch(Exception error){plc.Add(Failure("export tags "+table.Name,error));}
        }
        foreach(PlcTagTableUserGroup nested in group.Groups) Tags(nested,prefix,plc);
    }
    static void Types(PlcTypeGroup group,string prefix,XElement plc) {
        foreach(var type in group.Types) {
            string path=Path.Combine(output,prefix+"-type-"+(++sequence)+"-"+Safe(type.Name)+".xml");
            try {type.Export(new FileInfo(path),ExportOptions.WithDefaults);plc.Add(new XElement("DataType",new XAttribute("name",type.Name),new XAttribute("file",Path.GetFileName(path))));}
            catch(Exception error){plc.Add(Failure("export type "+type.Name,error));}
        }
        foreach(var nested in group.Groups) Types(nested,prefix,plc);
    }
    static void Technology(TechnologicalInstanceDBGroup group,string prefix,XElement plc) {
        foreach(var item in group.TechnologicalObjects) {
            var parameters=new XElement("TechnologyParameters",new XAttribute("name",item.Name),new XAttribute("validated",false),new XAttribute("type",item.OfSystemLibElement),new XAttribute("version",item.OfSystemLibVersion));
            try {
                foreach(var parameter in item.Parameters) {
                    try {parameters.Add(new XElement("Parameter",new XAttribute("name",parameter.Name),Convert.ToString(parameter.Value,System.Globalization.CultureInfo.InvariantCulture)));}
                    catch(Exception error){parameters.Add(Failure("read parameter "+parameter.Name,error));}
                }
            } catch(Exception error){parameters.Add(Failure("read parameters",error));}
            new XDocument(parameters).Save(Path.Combine(output,prefix+"-parameters-"+Safe(item.Name)+".xml"));
            string path=Path.Combine(output,prefix+"-technology-"+(++sequence)+"-"+Safe(item.Name)+".xml");
            try {item.Export(new FileInfo(path),ExportOptions.WithDefaults);plc.Add(new XElement("TechnologyObject",new XAttribute("name",item.Name),new XAttribute("file",Path.GetFileName(path))));}
            catch(Exception error){plc.Add(Failure("export technology "+item.Name,error));}
        }
        foreach(var nested in group.Groups) Technology(nested,prefix,plc);
    }
    static void Items(DeviceItem item, string devicePath) {
        string path=devicePath+"/"+item.Name;
        var hardware=new XElement("DeviceItem",new XAttribute("path",path));
        foreach(string key in new[]{"TypeIdentifier","OrderNumber","FirmwareVersion"}) {
            try {hardware.Add(new XElement("Attribute",new XAttribute("name",key),Convert.ToString(item.GetAttribute(key))));}
            catch { /* Unsupported attribute on this particular hardware item. */ }
        }
        report.Add(hardware);
        try {
            SoftwareContainer container=((IEngineeringServiceProvider)item).GetService<SoftwareContainer>();
            PlcSoftware software=container == null ? null : container.Software as PlcSoftware;
            if(software != null && exported.Add(path)) {
                XElement plc=new XElement("PLC",new XAttribute("path",path),new XAttribute("name",software.Name));report.Add(plc);
                string prefix="plc-"+exported.Count+"-"+Safe(software.Name);
                Console.WriteLine("EXPORT "+path);
                Blocks(software.BlockGroup,prefix,plc);Tags(software.TagTableGroup,prefix,plc);
                Types(software.TypeGroup,prefix,plc);Technology(software.TechnologicalObjectGroup,prefix,plc);
                new XDocument(new XElement(report)).Save(Path.Combine(output,"engineering-progress.xml"));
                if(compileEnabled) try {
                    Console.WriteLine("COMPILE "+path);
                    ICompilable compiler=((IEngineeringServiceProvider)software).GetService<ICompilable>();
                    CompilerResult result=compiler.Compile();
                    XElement compiled=new XElement("Compile",new XAttribute("state",result.State),new XAttribute("errors",result.ErrorCount),new XAttribute("warnings",result.WarningCount));
                    foreach(CompilerResultMessage message in result.Messages) compiled.Add(Message(message));
                    plc.Add(compiled);
                } catch(Exception error){plc.Add(Failure("compile",error));}
            }
        } catch(Exception error){report.Add(Failure("inspect "+path,error));}
        foreach(DeviceItem child in item.DeviceItems) Items(child,path);
    }
    static void Device(Device device) {
        foreach(DeviceItem item in device.DeviceItems) Items(item,device.Name);
    }
    static void Group(DeviceUserGroup group) {
        foreach(Device device in group.Devices) Device(device);
        foreach(DeviceUserGroup nested in group.Groups) Group(nested);
    }
    public static string Run(string projectFile,string outputDirectory,string allowedRoot,bool performCompile) {
        compileEnabled=performCompile;
        string full=Path.GetFullPath(projectFile);
        string boundary=Path.GetFullPath(allowedRoot).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;
        if(!full.StartsWith(boundary,StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Only the dedicated copied simulation project may be opened.");
        output=Path.GetFullPath(outputDirectory);Directory.CreateDirectory(output);
        report=new XElement("EngineeringExport",new XAttribute("time",DateTime.UtcNow.ToString("o")),new XAttribute("source",full),new XAttribute("compileRequested",performCompile),new XAttribute("originalProgramExecuted",false));
        if(!String.IsNullOrEmpty(CompileBlockName)) report.Add(new XAttribute("compileBlockRequested",CompileBlockName));
        exported=new HashSet<string>();sequence=0;
        try {
            using(TiaPortal portal=new TiaPortal(TiaPortalMode.WithoutUserInterface)) {
                Console.WriteLine("OPEN "+full);
                Project project=Path.GetExtension(full).Equals(".ap20",StringComparison.OrdinalIgnoreCase) ? portal.Projects.Open(new FileInfo(full)) : portal.Projects.OpenWithUpgrade(new FileInfo(full));
                try {
                    report.Add(new XElement("Project",new XAttribute("name",project.Name)));
                    foreach(Device device in project.Devices) Device(device);
                    foreach(DeviceUserGroup group in project.DeviceGroups) Group(group);
                    foreach(Device device in project.UngroupedDevicesGroup.Devices) Device(device);
                    project.Save();
                } finally {project.Close();}
            }
        } catch(Exception error) {report.Add(Failure("open/upgrade project",error));}
        int successfulFiles=System.Linq.Enumerable.Count(System.Linq.Enumerable.Where(report.Descendants(),n=>n.Attribute("file")!=null));
        report.Add(new XElement("Summary",new XAttribute("plcCount",exported.Count),new XAttribute("exportAttemptCount",sequence),new XAttribute("exportedFileCount",successfulFiles)));
        string reportPath=Path.Combine(output,"engineering-report.xml");new XDocument(report).Save(reportPath);return reportPath;
    }
}
