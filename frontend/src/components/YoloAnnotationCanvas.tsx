import React, { useEffect, useRef, useState } from 'react'

export type YoloBox = {
  class_key: string
  x: number
  y: number
  width: number
  height: number
  confidence?: number | null
}

type Interaction = {
  type: 'draw' | 'move' | 'resize'
  index: number
  startX: number
  startY: number
  original?: YoloBox
}

export default function YoloAnnotationCanvas({
  imageUrl,
  imageWidth,
  imageHeight,
  boxes,
  selectedClass,
  classLabels,
  onChange,
}: {
  imageUrl: string
  imageWidth: number
  imageHeight: number
  boxes: YoloBox[]
  selectedClass: string
  classLabels: Record<string, string>
  onChange: (boxes: YoloBox[]) => void
}) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const interaction = useRef<Interaction | null>(null)

  useEffect(() => {
    if (selected !== null && selected >= boxes.length) setSelected(null)
  }, [boxes.length, selected])

  const point = (event: React.PointerEvent<SVGSVGElement>) => {
    const rect = svgRef.current!.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(imageWidth, (event.clientX - rect.left) * imageWidth / rect.width)),
      y: Math.max(0, Math.min(imageHeight, (event.clientY - rect.top) * imageHeight / rect.height)),
    }
  }

  const beginDraw = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.target !== event.currentTarget) return
    const cursor = point(event)
    const index = boxes.length
    interaction.current = { type: 'draw', index, startX: cursor.x, startY: cursor.y }
    setSelected(index)
    onChange([...boxes, { class_key: selectedClass, x: cursor.x, y: cursor.y, width: 0, height: 0 }])
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const beginEdit = (event: React.PointerEvent<SVGElement>, index: number, type: 'move' | 'resize') => {
    event.stopPropagation()
    const rect = svgRef.current!.getBoundingClientRect()
    const cursor = {
      x: (event.clientX - rect.left) * imageWidth / rect.width,
      y: (event.clientY - rect.top) * imageHeight / rect.height,
    }
    interaction.current = { type, index, startX: cursor.x, startY: cursor.y, original: { ...boxes[index] } }
    setSelected(index)
    svgRef.current?.setPointerCapture(event.pointerId)
  }

  const move = (event: React.PointerEvent<SVGSVGElement>) => {
    const active = interaction.current
    if (!active) return
    const cursor = point(event)
    const next = boxes.map(box => ({ ...box }))
    if (!next[active.index]) return
    if (active.type === 'draw') {
      next[active.index].x = Math.min(active.startX, cursor.x)
      next[active.index].y = Math.min(active.startY, cursor.y)
      next[active.index].width = Math.abs(cursor.x - active.startX)
      next[active.index].height = Math.abs(cursor.y - active.startY)
    } else if (active.original) {
      const dx = cursor.x - active.startX
      const dy = cursor.y - active.startY
      if (active.type === 'move') {
        next[active.index].x = Math.max(0, Math.min(imageWidth - active.original.width, active.original.x + dx))
        next[active.index].y = Math.max(0, Math.min(imageHeight - active.original.height, active.original.y + dy))
      } else {
        next[active.index].width = Math.max(2, Math.min(imageWidth - active.original.x, active.original.width + dx))
        next[active.index].height = Math.max(2, Math.min(imageHeight - active.original.y, active.original.height + dy))
      }
    }
    onChange(next)
  }

  const end = () => {
    const active = interaction.current
    interaction.current = null
    if (active?.type === 'draw') {
      const box = boxes[active.index]
      if (box && (box.width < 2 || box.height < 2)) onChange(boxes.filter((_, index) => index !== active.index))
    }
  }

  const updateSelectedClass = (classKey: string) => {
    if (selected === null) return
    onChange(boxes.map((box, index) => index === selected ? { ...box, class_key: classKey } : box))
  }

  const deleteSelected = () => {
    if (selected === null) return
    onChange(boxes.filter((_, index) => index !== selected))
    setSelected(null)
  }

  return (
    <div>
      <div style={{ position: 'relative', width: '100%', aspectRatio: `${imageWidth}/${imageHeight}`, background: '#07111f', userSelect: 'none' }}>
        <img src={imageUrl} alt="YOLO annotation" draggable={false} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
        <svg
          ref={svgRef}
          viewBox={`0 0 ${imageWidth} ${imageHeight}`}
          preserveAspectRatio="none"
          onPointerDown={beginDraw}
          onPointerMove={move}
          onPointerUp={end}
          onPointerCancel={end}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', cursor: 'crosshair', touchAction: 'none' }}
        >
          {boxes.map((box, index) => {
            const active = selected === index
            return (
              <g key={index}>
                <rect x={box.x} y={box.y} width={box.width} height={box.height} fill={active ? 'rgba(250,204,21,.12)' : 'rgba(34,197,94,.08)'} stroke={active ? '#facc15' : '#22c55e'} strokeWidth={Math.max(2, imageWidth / 500)} onPointerDown={event => beginEdit(event, index, 'move')} style={{ cursor: 'move' }} />
                <rect x={box.x} y={Math.max(0, box.y - imageHeight * .035)} width={Math.max(box.width, imageWidth * .12)} height={imageHeight * .035} fill={active ? '#ca8a04' : '#15803d'} pointerEvents="none" />
                <text x={box.x + 4} y={Math.max(imageHeight * .025, box.y - imageHeight * .009)} fill="white" fontSize={Math.max(11, imageWidth / 70)} pointerEvents="none">{classLabels[box.class_key] ?? box.class_key}{box.confidence != null ? ` ${box.confidence.toFixed(2)}` : ''}</text>
                {active && <rect x={box.x + box.width - imageWidth * .012} y={box.y + box.height - imageHeight * .018} width={imageWidth * .024} height={imageHeight * .036} fill="#facc15" stroke="#111827" onPointerDown={event => beginEdit(event, index, 'resize')} style={{ cursor: 'nwse-resize' }} />}
              </g>
            )
          })}
        </svg>
      </div>
      <div style={{ display: 'flex', gap: 7, marginTop: 7, alignItems: 'center' }}>
        <span style={{ fontSize: 10, color: '#64748b' }}>빈 영역 드래그=생성 · Box 드래그=이동 · 우하단 핸들=Resize</span>
        <select disabled={selected === null} value={selected === null ? selectedClass : boxes[selected]?.class_key ?? selectedClass} onChange={event => updateSelectedClass(event.target.value)} style={{ marginLeft: 'auto', padding: 6 }}>
          {Object.entries(classLabels).map(([key, label]) => <option key={key} value={key}>{label} · {key}</option>)}
        </select>
        <button className="btn-warning" disabled={selected === null} onClick={deleteSelected}>선택 Box 삭제</button>
      </div>
    </div>
  )
}
