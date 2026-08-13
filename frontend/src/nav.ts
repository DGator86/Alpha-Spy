export interface NavItem {
  path: string
  label: string
}

export interface NavGroup {
  title: string
  items: NavItem[]
}

/** Sidebar structure. Route paths are the single source of truth for the router. */
export const NAV: NavGroup[] = [
  {
    title: 'Command',
    items: [
      { path: '/', label: 'Command Center' },
      { path: '/decision', label: 'Decision' },
    ],
  },
  {
    title: 'Market',
    items: [
      { path: '/market/spy', label: 'SPY' },
      { path: '/market/internals', label: 'Internals' },
      { path: '/market/regime', label: 'Regime' },
      { path: '/market/options', label: 'Options' },
    ],
  },
  {
    title: 'Trading',
    items: [
      { path: '/trading/opportunity', label: 'Opportunity' },
      { path: '/trading/active', label: 'Active Trade' },
      { path: '/trading/orders', label: 'Orders' },
      { path: '/trading/history', label: 'Trade History' },
    ],
  },
  {
    title: 'Intelligence',
    items: [
      { path: '/intelligence/forecasts', label: 'Forecasts' },
      { path: '/intelligence/pq', label: 'P vs Q' },
      { path: '/intelligence/models', label: 'Models' },
      { path: '/intelligence/calibration', label: 'Calibration' },
    ],
  },
  {
    title: 'Research',
    items: [
      { path: '/research/tape', label: 'Confirmation Tape' },
      { path: '/research/replay', label: 'Replay Lab' },
      { path: '/research/attribution', label: 'Attribution' },
    ],
  },
  {
    title: 'Governance',
    items: [
      { path: '/governance/validation', label: 'Paper Validation' },
      { path: '/governance/promotion', label: 'Promotion Gates' },
      { path: '/governance/audit', label: 'Audit Trail' },
    ],
  },
  {
    title: 'System',
    items: [
      { path: '/system/services', label: 'Services' },
      { path: '/system/feeds', label: 'Data Feeds' },
      { path: '/system/events', label: 'Event Calendar' },
      { path: '/system/broker', label: 'Broker' },
      { path: '/system/config', label: 'Configuration' },
      { path: '/system/security', label: 'Security' },
    ],
  },
]

export const TITLES: Record<string, string> = Object.fromEntries(
  NAV.flatMap((group) => group.items.map((item) => [item.path, item.label])),
)
