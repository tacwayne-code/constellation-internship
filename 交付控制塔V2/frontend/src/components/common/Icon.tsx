/**
 * 图标映射：业务图标名 → lucide-react
 */
import {
  LayoutGrid, Layers, FileText, Truck, Route, Box, Users, Handshake, Zap,
  Shield, Code2, CheckCircle2, FileBarChart, Search, Bell, Plus, Filter,
  Download, MoreHorizontal, BarChart3, ChevronRight, ArrowRight, Clock,
  Pin, AlertTriangle, X, Factory, Radar, RefreshCw,
  type LucideIcon,
} from 'lucide-react'

const MAP: Record<string, LucideIcon> = {
  grid: LayoutGrid,
  layers: Layers,
  file: FileText,
  truck: Truck,
  route: Route,
  box: Box,
  users: Users,
  handshake: Handshake,
  bolt: Zap,
  shield: Shield,
  code: Code2,
  check: CheckCircle2,
  report: FileBarChart,
  search: Search,
  bell: Bell,
  plus: Plus,
  filter: Filter,
  download: Download,
  more: MoreHorizontal,
  chart: BarChart3,
  chevron: ChevronRight,
  arrow: ArrowRight,
  clock: Clock,
  pin: Pin,
  alert: AlertTriangle,
  x: X,
  factory: Factory,
  radar: Radar,
  sync: RefreshCw,
}

interface IconProps {
  name: string
  size?: number
  className?: string
  style?: React.CSSProperties
}

export function Icon({ name, size = 16, className, style }: IconProps) {
  const Cmp = MAP[name] ?? Box
  return <Cmp size={size} className={className} style={style} />
}

export { MAP as ICON_MAP }
