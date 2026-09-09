export const initialTasks = [
 {id:1,title:'确认传感器替代方案',owner:'张工',due:'今天 18:00',priority:'高',status:'处理中',note:'核对替代传感器的接口与安装尺寸，确认后更新到货计划。'},
 {id:2,title:'完成A区货架安装',owner:'王师傅',due:'今天 20:00',priority:'中',status:'处理中',note:'立柱已完成 80%，横梁安装后由现场负责人复核。'},
 {id:3,title:'复核二层平台安装图',owner:'李工',due:'明天 12:00',priority:'中',status:'待处理',note:'与设计方确认平台开孔位置，正式确认前暂停相关安装。'},
 {id:4,title:'安排明日联调人员',owner:'陈工',due:'明天 18:00',priority:'低',status:'待处理',note:'安排电气、机械各两人参加现场联调。'},
 {id:5,title:'复查安全围栏固定点',owner:'王师傅',due:'09月11日',priority:'中',status:'待处理',note:'按现场检查表逐项复核固定点。'},
 {id:6,title:'完成电控柜接线检查',owner:'陈工',due:'09月12日',priority:'中',status:'处理中',note:'完成端子标识与接地检查。'},
 {id:7,title:'整理验收资料',owner:'林远',due:'09月16日',priority:'低',status:'待处理',note:'收集安装记录、测试报告和设备清单。'},
 {id:8,title:'确认客户验收时间',owner:'林远',due:'09月17日',priority:'中',status:'待处理',note:'联系客户确认参加人员及验收安排。'}
];
export const files = [
 {id:1,name:'二层平台安装图',version:'V3.0',type:'图纸',ext:'PDF',date:'今天 09:12',owner:'李工',state:'待确认',source:'项目资料 · 设计方提供'},
 {id:2,name:'设备到货清单_0905',version:'V1.0',type:'清单',ext:'XLSX',date:'昨天 16:40',owner:'张工',state:'可使用',source:'项目资料 · 采购整理'},
 {id:3,name:'A区货架安装图',version:'V2.1',type:'图纸',ext:'PDF',date:'09月05日',owner:'李工',state:'可使用',source:'项目资料 · 设计方提供'},
 {id:4,name:'现场安全检查表',version:'V1.0',type:'检查表',ext:'PDF',date:'09月03日',owner:'林远',state:'可使用',source:'项目资料 · 现场管理'}
];
export const purchases = [
 {id:'PO-260901',name:'输送线光电传感器',spec:'M18 · 对射型',qty:12,unit:'只',supplier:'苏州联科',date:'09月20日',status:'到货延迟',owner:'张工'},
 {id:'PO-260902',name:'电控柜端子排',spec:'UK-2.5B',qty:60,unit:'组',supplier:'昆山电气',date:'09月10日',status:'运输中',owner:'张工'},
 {id:'PO-260903',name:'A区货架横梁',spec:'L2700',qty:48,unit:'根',supplier:'常熟仓储',date:'09月08日',status:'已到货',owner:'王师傅'},
 {id:'PO-260904',name:'安全围栏组件',spec:'H1800',qty:24,unit:'套',supplier:'苏州安固',date:'09月11日',status:'待发货',owner:'张工'}
];
export const initialInventory = [
 {id:1,name:'A区货架横梁',spec:'L2700',unit:'根',qty:48,location:'A区 · 01号位'},
 {id:2,name:'膨胀螺栓',spec:'M12 × 100',unit:'套',qty:320,location:'工具仓 · 02号位'},
 {id:3,name:'电缆桥架',spec:'200 × 100',unit:'米',qty:86,location:'B区 · 03号位'},
 {id:4,name:'光电传感器',spec:'M18 · 对射型',unit:'只',qty:0,location:'电气仓 · 01号位'}
];
export const people = Array.from({length:12},(_,i)=>({id:i+1,name:['王建国','刘伟','周强','赵峰','陈明','孙杰','李成','吴刚','郑海','杨帆','徐亮','朱勇'][i],team:i<6?'机械安装组':'电气调试组',role:i===0||i===6?'班组长':i<6?'安装工':'电气工',task:i<6?'A区货架安装':'电控柜接线检查',state:'在场'}));
export const initialActivity = [{time:'今天 10:24',text:'A区货架立柱已完成 80%，进行横梁安装',owner:'王师傅'},{time:'今天 09:18',text:'供应商反馈：传感器预计 9月20日到货',owner:'张工'}];
