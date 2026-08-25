import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, CustomChart, HeatmapChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  HeatmapChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  DatasetComponent,
  GraphicComponent,
  MarkLineComponent,
  CanvasRenderer,
])

/** Shared axis/tooltip styling so every ECharts panel reads as one system. */
export const chartTheme = {
  axis: {
    axisLine: { lineStyle: { color: '#1b2937' } },
    axisTick: { show: false },
    axisLabel: { color: '#64798c', fontSize: 9, fontFamily: 'SFMono-Regular, Menlo, monospace' },
    splitLine: { lineStyle: { color: '#131f2a', type: 'dotted' as const } },
  },
  tooltip: {
    backgroundColor: '#0e1720',
    borderColor: '#263a4d',
    borderWidth: 1,
    textStyle: { color: '#e8f1f7', fontSize: 11, fontFamily: 'SFMono-Regular, Menlo, monospace' },
    extraCssText: 'border-radius:0;box-shadow:none;',
  },
}

export function EChart({
  option,
  className,
}: {
  option: echarts.EChartsCoreOption
  className?: string
}) {
  const host = useRef<HTMLDivElement | null>(null)
  const instance = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const element = host.current
    if (!element) return
    const chart = echarts.init(element, undefined, { renderer: 'canvas' })
    instance.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(element)
    return () => {
      observer.disconnect()
      chart.dispose()
      instance.current = null
    }
  }, [])

  useEffect(() => {
    // `true` replaces the option outright. Merging would leave series from a
    // previous render alive when a panel's data shrinks.
    instance.current?.setOption(option, true)
  }, [option])

  return <div ref={host} className={className} />
}
