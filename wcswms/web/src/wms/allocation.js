export const defaultAllocation={priority:'LEVEL_COLUMN',side_order:'LEFT_FIRST',level_order:'LOW_FIRST',column_order:'FRONT_FIRST'};
export const priorities={LEVEL_COLUMN:'先层数，再列号',COLUMN_LEVEL:'先列号，再层数'};
export const directions={side_order:{LEFT_FIRST:'先左后右',RIGHT_FIRST:'先右后左',BALANCED:'均分（一左一右交替）'},level_order:{LOW_FIRST:'先下后上',HIGH_FIRST:'先上后下'},column_order:{FRONT_FIRST:'先前后后（列号从小到大）',BACK_FIRST:'先后后前（列号从大到小）'}};
export function policyValues(policy){const value=Object.fromEntries(Object.entries(defaultAllocation).map(([key,fallback])=>[key,policy?.[key]??fallback]));if(!priorities[value.priority])value.priority=value.priority.indexOf('LEVEL')<value.priority.indexOf('COLUMN')?'LEVEL_COLUMN':'COLUMN_LEVEL';return value;}
export function allocationSummary(policy){const value=policyValues(policy);return `${directions.side_order[value.side_order]}；仓内${priorities[value.priority]}，${directions.level_order[value.level_order]}、${directions.column_order[value.column_order]}`;}
