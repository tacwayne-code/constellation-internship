using System;
using System.IO;
using System.Xml.Linq;
using Siemens.Engineering;
using Siemens.Engineering.HW;
using Siemens.Engineering.Download;
using Siemens.Engineering.Download.Configurations;

public static class BuildSimulationCard {
    static XElement report; static string output; static int count;
    static void Save(){new XDocument(report).Save(Path.Combine(output,"card-report.xml"));}
    static void Configure(DownloadConfiguration configuration){
        Console.WriteLine("CONFIG "+configuration.GetType().Name);
        report.Add(new XElement("Configuration",configuration.GetType().Name));Save();
        var target=configuration as TargetForSoftware;
        var overwrite=configuration as OverwriteOnMemoryCard;
        var blocks=configuration as ConsistentBlocksDownload;
        var alarms=configuration as AlarmTextLibrariesDownload;
        if(target!=null)target.CurrentSelection=TargetForSoftwareSelections.PlcSimulationAdvanced;
        else if(overwrite!=null)overwrite.CurrentSelection=OverwriteOnMemoryCardSelections.Load;
        else if(blocks!=null)blocks.CurrentSelection=ConsistentBlocksDownloadSelections.ConsistentDownload;
        else if(alarms!=null)alarms.CurrentSelection=AlarmTextLibrariesDownloadSelections.ConsistentDownload;
    }
    static XElement Message(DownloadResultMessage m){var x=new XElement("Message",new XAttribute("state",m.State),m.Message??"");foreach(var c in m.Messages)x.Add(Message(c));return x;}
    static void Item(DeviceItem item){
        var provider=((IEngineeringServiceProvider)item).GetService<DownloadProvider>();
        if(provider!=null && item.Name=="PLC_2"){
            if(++count!=1)throw new Exception("More than one matching CPU");
            var card=new DirectoryInfo(Path.GetFullPath(Path.Combine(output,"storage","SIMATIC_MC")).Replace('/', '\\'));
            card.Create();
            Console.WriteLine("BUILD CARD "+item.Name);Save();
            var result=provider.Download(card,Configure);
            var node=new XElement("Download",new XAttribute("state",result.State),new XAttribute("errors",result.ErrorCount),new XAttribute("warnings",result.WarningCount));
            foreach(var m in result.Messages)node.Add(Message(m));report.Add(node);Save();
            if(result.ErrorCount>0)throw new Exception("Card generation failed; see report");
        }
        foreach(var child in item.DeviceItems)Item(child);
    }
    static void Device(Device device){foreach(var item in device.DeviceItems)Item(item);}
    static void Group(DeviceUserGroup group){foreach(var d in group.Devices)Device(d);foreach(var g in group.Groups)Group(g);}
    public static void Run(string root,string directory){
        output=directory;Directory.CreateDirectory(output);count=0;
        report=new XElement("SimulationCard",new XAttribute("created",DateTime.UtcNow.ToString("o")),new XAttribute("target","PlcSimulationAdvanced"));Save();
        try{using(var tia=new TiaPortal(TiaPortalMode.WithoutUserInterface)){
            var path=Path.Combine(root,"data/siemens/projects/v20-simulation_V20/v20-simulation_V20.ap20");
            var project=tia.Projects.Open(new FileInfo(path));
            foreach(var d in project.Devices)Device(d);
            foreach(var g in project.DeviceGroups)Group(g);
            foreach(var d in project.UngroupedDevicesGroup.Devices)Device(d);
            if(count!=1)throw new Exception("Expected PLC_2 was not found");
            project.Save();project.Close();
        }}catch(Exception ex){report.Add(new XElement("Error",ex.ToString()));Save();throw;}
    }
}
