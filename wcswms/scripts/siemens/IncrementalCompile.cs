using System;
using System.IO;
using System.Linq;
using System.Xml.Linq;
using Siemens.Engineering;
using Siemens.Engineering.HW;
using Siemens.Engineering.HW.Features;
using Siemens.Engineering.SW;
using Siemens.Engineering.Compiler;

public static class IncrementalCompile {
    static XElement report;
    static string output;
    static void Save(){new XDocument(new XElement(report)).Save(Path.Combine(output,"incremental-report.xml"));}
    static XElement Message(CompilerResultMessage m){var n=new XElement("Message",new XAttribute("state",m.State),new XAttribute("path",m.Path??""),m.Description);foreach(var child in m.Messages)n.Add(Message(child));return n;}
    static PlcSoftware Find(DeviceItem item){var c=((IEngineeringServiceProvider)item).GetService<SoftwareContainer>();if(c!=null && c.Software is PlcSoftware)return (PlcSoftware)c.Software;foreach(var child in item.DeviceItems){var p=Find(child);if(p!=null)return p;}return null;}
    public static void Run(string directory,string sources){
        output=directory;Directory.CreateDirectory(output);
        report=new XElement("IncrementalTest",new XAttribute("time",DateTime.UtcNow.ToString("o")),new XAttribute("source",sources),new XAttribute("originalProgramExecuted",false));Save();
        try {using(var tia=new TiaPortal(TiaPortalMode.WithoutUserInterface)){
            var project=tia.Projects.Create(new DirectoryInfo(output),"IncrementalCPU1511");
            var device=project.Devices.CreateWithItem("OrderNumber:6ES7 511-1AL03-0AB0/V3.0","TestPLC","TestStation");
            PlcSoftware plc=null;foreach(var item in device.DeviceItems){plc=Find(item);if(plc!=null)break;}
            if(plc==null)throw new Exception("CPU software not found");
            report.Add(new XElement("Project",project.Path.FullName));project.Save();Save();
            foreach(string kind in new[]{"Baseline","SW.Tags.PlcTagTable","SW.Blocks.GlobalDB","SW.Blocks.FC","SW.Blocks.FB","SW.Blocks.InstanceDB","SW.Blocks.OB"}){
                var stage=new XElement("Stage",new XAttribute("kind",kind));report.Add(stage);Save();Console.WriteLine("STAGE "+kind);
                foreach(var file in Directory.GetFiles(sources,"plc-*.xml").OrderBy(p=>p)){
                    var doc=XDocument.Load(file);var block=doc.Root.Elements().FirstOrDefault(n=>n.Name.LocalName==kind);if(block==null)continue;
                    var name=(string)block.Element("AttributeList").Element("Name");
                    var entry=new XElement("Import",new XAttribute("name",name??""),new XAttribute("file",Path.GetFileName(file)));stage.Add(entry);Save();
                    try {if(kind=="SW.Tags.PlcTagTable")plc.TagTableGroup.TagTables.Import(new FileInfo(file),ImportOptions.Override);else plc.BlockGroup.Blocks.Import(new FileInfo(file),ImportOptions.Override);entry.Add(new XAttribute("success",true));}
                    catch(Exception ex){entry.Add(new XAttribute("success",false),new XElement("Error",ex.ToString()));}Save();
                }
                project.Save();stage.Add(new XElement("CompileStarted",DateTime.UtcNow.ToString("o")));Save();
                try {var result=((IEngineeringServiceProvider)plc).GetService<ICompilable>().Compile();var compile=new XElement("Compile",new XAttribute("state",result.State),new XAttribute("errors",result.ErrorCount),new XAttribute("warnings",result.WarningCount));foreach(var m in result.Messages)compile.Add(Message(m));stage.Add(compile);Console.WriteLine("RESULT "+kind+" errors="+result.ErrorCount+" warnings="+result.WarningCount);Save();}
                catch(Exception ex){stage.Add(new XElement("CompileException",ex.ToString()));Save();throw;}
            }
            project.Save();project.Close();
        }}catch(Exception ex){report.Add(new XElement("FatalError",ex.ToString()));Save();throw;}
    }
}
