using System;
using System.IO;
using System.Xml.Linq;
using Siemens.Engineering;
using Siemens.Engineering.HW;
using Siemens.Engineering.HW.Features;
using Siemens.Engineering.SW;
using Siemens.Engineering.Compiler;

public static class MinimalCompile {
    static XElement report;
    static string reportPath;
    static void Save(){new XDocument(new XElement(report)).Save(reportPath);}
    static void Visit(DeviceItem item) {
        var container=((IEngineeringServiceProvider)item).GetService<SoftwareContainer>();
        var software=container==null?null:container.Software as PlcSoftware;
        if(software!=null) {
            var node=new XElement("PLC",new XAttribute("name",software.Name));report.Add(node);
            node.Add(new XElement("CompileStarted",DateTime.UtcNow.ToString("o")));Save();
            Console.WriteLine("COMPILE MINIMAL "+software.Name);
            try {
                var result=((IEngineeringServiceProvider)software).GetService<ICompilable>().Compile();
                var compiled=new XElement("Compile",new XAttribute("state",result.State),new XAttribute("errors",result.ErrorCount),new XAttribute("warnings",result.WarningCount));
                foreach(var message in result.Messages) compiled.Add(new XElement("Message",message.Description));
                node.Add(compiled);Save();
            } catch(Exception ex){node.Add(new XElement("Error",ex.ToString()));Save();throw;}
        }
        foreach(var child in item.DeviceItems) Visit(child);
    }
    public static void Run(string directory) {
        Directory.CreateDirectory(directory);reportPath=Path.Combine(directory,"minimal-compile-report.xml");
        report=new XElement("MinimalCompile",new XAttribute("created",DateTime.UtcNow.ToString("o")),new XAttribute("originalProgramExecuted",false));Save();
        try {
            using(var portal=new TiaPortal(TiaPortalMode.WithoutUserInterface)) {
                var project=portal.Projects.Create(new DirectoryInfo(directory),"MinimalCPU1511");
                Console.WriteLine("CREATE CPU1511 V3.0");
                var device=project.Devices.CreateWithItem("OrderNumber:6ES7 511-1AL03-0AB0/V3.0","MinimalPLC","MinimalStation");
                project.Save();report.Add(new XElement("CreatedProject",project.Path.FullName));Save();
                foreach(var item in device.DeviceItems) Visit(item);
                project.Save();project.Close();
            }
        } catch(Exception ex){report.Add(new XElement("Error",ex.ToString()));Save();throw;}
    }
}
