"""
物料编码生成器 — PLM 编码体系 v1.0 (2026-07-29)

配件编码格式：  AA-BB-CC-DDD
  AA  = 大类码（01=设备配件, 02=钣金件, 03=电气件, 04=焊接结构件, 05=气动/液压件）
  BB  = 产品族码（01=分光机, 02=编带机, 03=点胶机, 04=固晶机, 05=搅拌机, 06=贴片机, 99=通用件）
  CC  = 部件分类码（01=机架底座, 02=传动机构, 03=送料机构, 04=光学组件, 05=电气安装, 06=气动组件,
                    07=管路接头, 08=防护罩壳, 09=工装治具, 10=焊接结构）
  DD = 流水号 01~99

标准件编码格式： ST-XX-XXX-XXXX
  ST  = 固定前缀
  XX  = 大类（01=螺钉/螺栓, 02=螺母/垫圈, 03=轴承, 04=销/键, 05=卡簧/挡圈, 06=弹簧,
              07=密封件, 08=气管/接头, 09=电气标准件, 10=五金杂项）
  XXX = 具体类别序号
  XXXX = 规格代码（如 M040 = M4×20）
"""

# ── 映射表 ──

PART_CATEGORY = {
    '01': '设备配件',
    '02': '钣金件',
    '03': '电气件',
    '04': '焊接结构件',
    '05': '气动/液压件',
}

PRODUCT_FAMILY = {
    '01': '分光机',      # 710, 835分光
    '02': '编带机',      # 835编带, 910
    '03': '点胶机',      # 812, 813, 820, 830, 838, 850, 860
    '04': '固晶机',      # 1109MT
    '05': '搅拌机',
    '06': '贴片机',      # 860贴片
    '99': '通用件',
}

COMPONENT_CATEGORY = {
    '01': '机架/底座',
    '02': '传动机构',
    '03': '送料机构',
    '04': '光学组件',
    '05': '电气安装',
    '06': '气动组件',
    '07': '管路/接头',
    '08': '防护/罩壳',
    '09': '工装/治具',
    '10': '焊接结构',
}

STD_CATEGORY = {
    '01': '螺钉/螺栓',
    '02': '螺母/垫圈',
    '03': '轴承',
    '04': '销/键',
    '05': '卡簧/挡圈',
    '06': '弹簧',
    '07': '密封件',
    '08': '气管/接头',
    '09': '电气标准件',
    '10': '五金杂项',
}

# ── 产品名 → 产品族码映射 ──
# 从 BOM 名称 / Product name 推断产品族码
def infer_family_code(product_name: str) -> str:
    """根据产品名称推断产品族码"""
    name = product_name.lower() if product_name else ''
    if '分光' in name:
        return '01'
    if '编带' in name:
        return '02'
    if '点胶' in name:
        return '03'
    if '固晶' in name:
        return '04'
    if '搅拌' in name:
        return '05'
    if '贴片' in name:
        return '06'
    return '99'  # 通用


def generate_part_code(model, family_code: str, component_code: str, category_code: str = '01') -> str:
    """
    生成配件编码：AA-BB-CC-DDD
    model   = SQLAlchemy db.model (BomItem)
    返回新编码（如 '01-01-04-003'）
    """
    prefix = f'{category_code}-{family_code}-{component_code}-'

    # 找同一前缀下最大流水号
    from app import db
    last = model.query.filter(model.code.like(prefix + '%')).order_by(model.code.desc()).first()
    if last and last.code:
        try:
            seq = int(last.code[-3:]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1

    if seq > 99:
        raise ValueError(f'流水号已满（>99），前缀 {prefix}')

    return f'{prefix}{seq:02d}'


def generate_std_code(model, std_category: str, sub_category: str, spec: str) -> str:
    """
    生成标准件编码：ST-XX-XXX-XXXX
    model        = SQLAlchemy db.model (BomItem)
    std_category = 标准件大类（01~10）
    sub_category = 具体类别（如 001=内六角圆柱头）
    spec         = 规格代码（如 M040 = M4×20）
    """
    return f'ST-{std_category}-{sub_category}-{spec}'


def get_all_mappings():
    """返回所有映射表供前端使用"""
    return {
        'part_category': PART_CATEGORY,
        'product_family': PRODUCT_FAMILY,
        'component_category': COMPONENT_CATEGORY,
        'std_category': STD_CATEGORY,
        'family_index': list(PRODUCT_FAMILY.keys()),
        'component_index': list(COMPONENT_CATEGORY.keys()),
    }
