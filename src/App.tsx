import { useEffect, useMemo, useState } from 'react'
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  CircleDollarSign,
  Crosshair,
  KeyRound,
  MapPin,
  PackageCheck,
  ReceiptText,
  RefreshCw,
  Route,
  Search,
  ShoppingCart,
  Store as StoreIcon,
  Truck,
} from 'lucide-react'
import {
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  useMap,
  ZoomControl,
} from 'react-leaflet'
import { divIcon } from 'leaflet'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'

type ReceiptLine = {
  sku: string
  name: string
  quantity: number
  buyPrice: number
  targetPrice: number
  imageUrl: string
}

type PurchaseReceipt = {
  receiptId: string
  destinationStoreId: string
  destinationStoreName: string
  units: number
  lines: ReceiptLine[]
}

type DirectPurchase = {
  sku: string
  name: string
  quantity: number
  price: number
  retailPrice: number
  itemLocation: string
  imageUrl: string
}

type PriceAdjustment = {
  receiptId: string
  sourceStoreId: string
  sourceStoreName: string
  units: number
  lines: ReceiptLine[]
}

type Stop = {
  sequence: number
  storeId: string
  storeName: string
  address: string
  latitude: number
  longitude: number
  legMiles: number
  cumulativeMiles: number
  roles: string[]
  directPurchases: DirectPurchase[]
  purchaseReceipts: PurchaseReceipt[]
  priceAdjustment: PriceAdjustment | null
  carryingAfter: { receipts: number; units: number }
}

type AvailableStore = {
  storeId: string
  storeName: string
  address: string
  state: string
  latitude: number
  longitude: number
  distanceMiles: number | null
  stockedSkuCount: number
  clearanceSkuCount: number
}

type PreviewStore = {
  catalogId: string
  storeId: string
  storeName: string
  address: string
  state: string
  latitude: number
  longitude: number
  distanceMiles: number
  selectable: boolean
}

type StoreSearchResult = {
  homeZip: string
  radiusMiles: number
  homeCoordinate: [number, number]
  stores: PreviewStore[]
  truncated: boolean
  source: string
}

type StoreMode = 'optimized' | 'selected' | 'all'

type PlanResult = {
  homeZip: string
  checkedAt: string
  summary: {
    totalMiles: number
    returnMiles: number
    storesVisited: number
    totalUnits: number
    directUnits: number
    restockUnits: number
    totalSavings: number
  }
  stops: Stop[]
  routeCoordinates: [number, number][]
  warnings: string[]
  availableStores: AvailableStore[]
  selectionMode: 'optimized' | 'custom'
  recommendedStoreIds: string[]
}

type PlanJob = {
  id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  result: PlanResult | null
  error: string | null
}

const SKU_STORAGE_KEY = 'stockpath.skus'
const TOKEN_STORAGE_KEY = 'stockpath.hiddenClearancesToken'
const EMPTY_STOPS: Stop[] = []

function readSavedValue(key: string) {
  try {
    return window.localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function formatMoney(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value)
}

function parseSkus(value: string) {
  return [...new Set(value.split(/[\s,]+/).map((sku) => sku.replace(/\D/g, '')).filter(Boolean))]
}

type MapMarkerKind = 'available' | 'selected' | 'route' | 'match' | 'focused' | 'home' | 'disabled'

function createMapMarker(kind: MapMarkerKind, label = '') {
  return divIcon({
    className: 'stockpath-map-icon',
    html: `<span class="stockpath-marker stockpath-marker--${kind}">${label}</span>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -15],
  })
}

function MapViewport({
  coordinates,
  focusedStop,
  stops,
}: {
  coordinates: [number, number][]
  focusedStop: number | null
  stops: Stop[]
}) {
  const map = useMap()

  useEffect(() => {
    if (focusedStop !== null) {
      const stop = stops.find((candidate) => candidate.sequence === focusedStop)
      if (stop) map.flyTo([stop.latitude, stop.longitude], 11, { duration: 0.45 })
      return
    }
    if (coordinates.length > 1) {
      map.fitBounds(coordinates, { paddingTopLeft: [44, 60], paddingBottomRight: [44, 44], maxZoom: 10 })
    }
  }, [coordinates, focusedStop, map, stops])

  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }))
    observer.observe(map.getContainer())
    return () => observer.disconnect()
  }, [map])

  return null
}

function RouteMap({
  result,
  preview,
  storeMode,
  focusedStop,
  selectedStoreIds,
  onToggleStore,
  onFocusStop,
}: {
  result: PlanResult | null
  preview: StoreSearchResult | null
  storeMode: StoreMode
  focusedStop: number | null
  selectedStoreIds: Set<string>
  onToggleStore: (storeId: string) => void
  onFocusStop: (sequence: number) => void
}) {
  const center: [number, number] = result?.routeCoordinates[0] ?? preview?.homeCoordinate ?? [39.4143, -77.4105]
  const routeStops = useMemo(
    () => new Map(result?.stops.map((stop) => [stop.storeId, stop]) ?? []),
    [result],
  )
  const mapStores = useMemo(() => {
    if (result) {
      return result.availableStores.map((store) => ({
        ...store,
        catalogId: store.storeId,
        selectable: true,
      }))
    }
    return (preview?.stores ?? []).map((store) => ({
      ...store,
      stockedSkuCount: 0,
      clearanceSkuCount: 0,
    }))
  }, [preview, result])
  const homeCoordinate = result?.routeCoordinates[0] ?? preview?.homeCoordinate
  const boundsCoordinates = useMemo<[number, number][]>(() => {
    if (result?.routeCoordinates.length) return result.routeCoordinates
    if (!preview) return []
    return [
      preview.homeCoordinate,
      ...preview.stores.map((store) => [store.latitude, store.longitude] as [number, number]),
    ]
  }, [preview, result])

  return (
    <MapContainer center={center} zoom={8} zoomControl={false} className="h-full w-full">
      <ZoomControl position="bottomleft" />
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
      />
      {boundsCoordinates.length > 1 && (
        <MapViewport coordinates={boundsCoordinates} focusedStop={focusedStop} stops={result?.stops ?? EMPTY_STOPS} />
      )}
      {homeCoordinate && (
        <>
          {result && (
            <>
              <Polyline
                positions={result.routeCoordinates}
                smoothFactor={2.5}
                pathOptions={{ color: '#ffffff', weight: 6, opacity: 0.9, lineJoin: 'round', lineCap: 'round' }}
              />
              <Polyline
                positions={result.routeCoordinates}
                smoothFactor={2.5}
                pathOptions={{ color: '#2563eb', weight: 3, opacity: 0.9, lineJoin: 'round', lineCap: 'round' }}
              />
            </>
          )}
          <Marker position={homeCoordinate} icon={createMapMarker('home', 'S')} zIndexOffset={2000}>
            <Popup>Start and finish · {result?.homeZip ?? preview?.homeZip}</Popup>
          </Marker>
          {mapStores.map((store) => {
            const stop = routeStops.get(store.storeId)
            const isFocused = stop?.sequence === focusedStop
            const isManualSelection = !result && storeMode === 'selected' && selectedStoreIds.has(store.catalogId)
            const isRequired = !result && storeMode === 'all' && store.selectable
            const isRouteStop = Boolean(stop)
            const highlighted = isRouteStop || isManualSelection || isRequired
            const hasMatch = Boolean(stop?.priceAdjustment)
            const markerKind: MapMarkerKind = !store.selectable
              ? 'disabled'
              : isFocused
                ? 'focused'
                : hasMatch
                  ? 'match'
                  : isRouteStop
                    ? 'route'
                    : highlighted
                      ? 'selected'
                      : 'available'

            return (
              <Marker
                key={store.catalogId}
                position={[store.latitude, store.longitude]}
                icon={createMapMarker(markerKind, stop ? String(stop.sequence) : '')}
                zIndexOffset={isRouteStop ? 1000 + (stop?.sequence ?? 0) : 0}
                eventHandlers={{
                  click: () => {
                    if (result && stop) onFocusStop(stop.sequence)
                    else if (!result && storeMode === 'selected' && store.selectable) onToggleStore(store.catalogId)
                  },
                }}
              >
                <Popup>
                  <strong>{stop ? `${stop.sequence}. ` : ''}{store.storeName}</strong><br />
                  {store.address}<br />
                  {store.distanceMiles == null ? '' : `${store.distanceMiles.toFixed(1)} mi from start`}
                  {result && <><br />{store.stockedSkuCount} stocked SKU{store.stockedSkuCount === 1 ? '' : 's'}</>}
                  <br />
                  <span className="map-popup-status">
                    {!store.selectable
                      ? 'Store number unavailable'
                      : result
                        ? stop ? 'Route stop · click to focus' : 'Not used in this route'
                        : storeMode === 'selected'
                          ? isManualSelection ? 'Selected · click to remove' : 'Click to select'
                          : storeMode === 'all'
                            ? 'Required stop'
                            : 'Eligible for optimization'}
                  </span>
                </Popup>
              </Marker>
            )
          })}
        </>
      )}
    </MapContainer>
  )
}

function ProductThumb({ src, name }: { src: string; name: string }) {
  const [failed, setFailed] = useState(false)

  if (!src || failed) {
    return (
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md border border-slate-700/70 bg-slate-900 text-slate-600">
        <ShoppingCart className="size-3.5" />
      </span>
    )
  }

  return (
    <img
      src={src}
      alt={name}
      loading="lazy"
      onError={() => setFailed(true)}
      className="size-9 shrink-0 rounded-md border border-slate-700/70 bg-white object-contain p-0.5"
    />
  )
}

function ItemLine({ line, adjustment = false }: { line: ReceiptLine; adjustment?: boolean }) {
  return (
    <div className="grid grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-2 py-2 text-xs">
      <span className="font-semibold tabular-nums text-blue-300">{line.quantity}×</span>
      <ProductThumb src={line.imageUrl} name={line.name} />
      <div className="min-w-0">
        <p className="truncate text-slate-200">{line.name}</p>
        <p className="font-mono text-[10px] text-slate-500">SKU {line.sku}</p>
      </div>
      <div className="text-right tabular-nums">
        <p className={adjustment ? 'font-semibold text-cyan-300' : 'text-slate-300'}>
          ${adjustment ? line.targetPrice.toFixed(2) : line.buyPrice.toFixed(2)}
        </p>
        {adjustment && <p className="text-[10px] text-slate-600 line-through">${line.buyPrice.toFixed(2)}</p>}
      </div>
    </div>
  )
}

function StopCard({
  stop,
  expanded,
  focused,
  onActivate,
}: {
  stop: Stop
  expanded: boolean
  focused: boolean
  onActivate: () => void
}) {
  const actionCount = stop.directPurchases.length + stop.purchaseReceipts.length + (stop.priceAdjustment ? 1 : 0)

  return (
    <article className={`border-b border-slate-800/80 transition-colors ${focused ? 'bg-blue-500/[0.06]' : 'hover:bg-slate-900/45'}`}>
      <button type="button" onClick={onActivate} className="flex w-full items-start gap-3 px-4 py-3.5 text-left">
        <span className={`mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${focused ? 'bg-blue-500 text-white' : 'bg-slate-800 text-slate-300'}`}>
          {stop.sequence}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-slate-100">{stop.storeName}</span>
            {stop.priceAdjustment && <span className="size-1.5 shrink-0 rounded-full bg-cyan-400" />}
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-slate-500">{stop.address}</span>
          <span className="mt-1.5 flex flex-wrap gap-1.5">
            {stop.roles.map((role) => (
              <Badge key={role} variant="secondary" className="h-4 rounded-sm border border-slate-700/70 bg-transparent px-1.5 text-[8px] uppercase tracking-[0.08em] text-slate-400">
                {role}
              </Badge>
            ))}
          </span>
        </span>
        <span className="shrink-0 text-right">
          <span className="block text-sm font-semibold tabular-nums text-blue-300">{stop.legMiles} mi</span>
          <span className="text-[10px] text-slate-600">{actionCount} action{actionCount === 1 ? '' : 's'}</span>
        </span>
        {expanded ? <ChevronUp className="mt-1 size-4 text-slate-600" /> : <ChevronDown className="mt-1 size-4 text-slate-600" />}
      </button>

      {expanded && (
        <div className="space-y-4 border-t border-slate-800/70 bg-[#090e17]/70 px-4 py-4 pl-13">
          {stop.directPurchases.length > 0 && (
            <section>
              <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.13em] text-blue-300">
                <ShoppingCart className="size-3" /> Buy clearance here
              </p>
              <div className="mt-1 divide-y divide-slate-800/70">
                {stop.directPurchases.map((line) => (
                  <div key={line.sku} className="grid grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-2 py-2 text-xs">
                    <span className="font-semibold text-blue-300">{line.quantity}×</span>
                    <ProductThumb src={line.imageUrl} name={line.name} />
                    <div className="min-w-0">
                      <p className="truncate text-slate-200">{line.name}</p>
                      <p className="font-mono text-[10px] text-slate-500">
                        SKU {line.sku}{line.itemLocation ? ` · ${line.itemLocation}` : ''}
                      </p>
                    </div>
                    <p className="font-semibold tabular-nums text-cyan-300">${line.price.toFixed(2)}</p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {stop.purchaseReceipts.map((receipt) => (
            <section key={receipt.receiptId} className="border-l-2 border-blue-500/70 pl-3">
              <div className="flex items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.13em] text-blue-300">
                  <ReceiptText className="size-3" /> Create receipt {receipt.receiptId}
                </p>
                <span className="text-[10px] tabular-nums text-slate-500">{receipt.units} units</span>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">Keep together for <span className="text-slate-300">{receipt.destinationStoreName}</span></p>
              <div className="mt-1 divide-y divide-slate-800/70">
                {receipt.lines.map((line) => <ItemLine key={line.sku} line={line} />)}
              </div>
            </section>
          ))}

          {stop.priceAdjustment && (
            <section className="border-l-2 border-cyan-500/70 pl-3">
              <div className="flex items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.13em] text-cyan-300">
                  <PackageCheck className="size-3" /> Price match {stop.priceAdjustment.receiptId}
                </p>
                <span className="text-[10px] tabular-nums text-cyan-300">{stop.priceAdjustment.units} units</span>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">Receipt from {stop.priceAdjustment.sourceStoreName}</p>
              <div className="mt-1 divide-y divide-slate-800/70">
                {stop.priceAdjustment.lines.map((line) => <ItemLine key={line.sku} line={line} adjustment />)}
              </div>
            </section>
          )}

          <div className="flex items-center justify-between border-t border-slate-800/70 pt-2 text-[10px] text-slate-600">
            <span>{stop.cumulativeMiles} miles from start</span>
            <span>{stop.carryingAfter.units} units awaiting match</span>
          </div>
        </div>
      )}
    </article>
  )
}

function SectionHeading({ number, title, detail }: { number: string; title: string; detail?: string }) {
  return (
    <div className="mb-3 flex items-start gap-2.5">
      <span className="mt-0.5 font-mono text-[10px] font-semibold text-blue-400">{number}</span>
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-300">{title}</h2>
        {detail && <p className="mt-0.5 text-[10px] leading-4 text-slate-600">{detail}</p>}
      </div>
    </div>
  )
}

function App() {
  const [homeZip, setHomeZip] = useState('21704')
  const [skuText, setSkuText] = useState(() => readSavedValue(SKU_STORAGE_KEY))
  const [bearerToken, setBearerToken] = useState(() => readSavedValue(TOKEN_STORAGE_KEY))
  const [restockLimit, setRestockLimit] = useState(6)
  const [maxRadiusMiles, setMaxRadiusMiles] = useState(50)
  const [jobId, setJobId] = useState<string | null>(null)
  const [pollVersion, setPollVersion] = useState(0)
  const [job, setJob] = useState<PlanJob | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [expandedStop, setExpandedStop] = useState<number | null>(null)
  const [focusedStop, setFocusedStop] = useState<number | null>(null)
  const [selectedStoreIds, setSelectedStoreIds] = useState<Set<string>>(new Set())
  const [storePreview, setStorePreview] = useState<StoreSearchResult | null>(null)
  const [storeMode, setStoreMode] = useState<StoreMode>('optimized')
  const [searchingStores, setSearchingStores] = useState(false)

  const skus = useMemo(() => parseSkus(skuText), [skuText])
  const result = job?.result ?? null
  const loading = submitting || job?.status === 'queued' || job?.status === 'running'
  const previewIsCurrent = Boolean(
    storePreview
    && storePreview.homeZip === homeZip
    && storePreview.radiusMiles === maxRadiusMiles,
  )

  useEffect(() => {
    if (!result || job?.status !== 'completed') return
    setExpandedStop(null)
    setFocusedStop(null)
  }, [job?.status, result])

  useEffect(() => {
    try {
      window.localStorage.setItem(SKU_STORAGE_KEY, skuText)
    } catch {
      // Browser policy may disable local storage.
    }
  }, [skuText])

  useEffect(() => {
    try {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, bearerToken)
    } catch {
      // Browser policy may disable local storage.
    }
  }, [bearerToken])

  useEffect(() => {
    if (!jobId) return
    let active = true
    let terminal = false
    const events = new EventSource(`/api/plans/${jobId}/events`)

    events.onmessage = (event) => {
      if (!active) return
      const nextJob = JSON.parse(event.data) as PlanJob
      setJob(nextJob)
      if (nextJob.status === 'failed') {
        setError(nextJob.error || 'Route generation failed')
        terminal = true
        events.close()
      } else {
        setError('')
      }
      if (nextJob.status === 'completed') {
        terminal = true
        events.close()
      }
    }

    events.onerror = () => {
      if (active && !terminal) setError('Planner connection interrupted; reconnecting…')
    }

    return () => {
      active = false
      events.close()
    }
  }, [jobId, pollVersion])

  const invalidateStorePreview = () => {
    setStorePreview(null)
    setSelectedStoreIds(new Set())
    setStoreMode('optimized')
    setJob(null)
    setJobId(null)
    setExpandedStop(null)
    setFocusedStop(null)
  }

  const findNearbyStores = async () => {
    if (searchingStores || loading) return
    setError('')
    if (!/^\d{5}$/.test(homeZip)) {
      setError('Enter a five-digit starting ZIP code.')
      return
    }
    setSearchingStores(true)
    try {
      const parameters = new URLSearchParams({ zip: homeZip, radiusMiles: String(maxRadiusMiles) })
      const response = await fetch(`/api/stores?${parameters}`)
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Could not find nearby stores')
      }
      const preview = await response.json() as StoreSearchResult
      if (preview.stores.length === 0) throw new Error('No Home Depot stores were found in this radius')
      setStorePreview(preview)
      setStoreMode('optimized')
      setSelectedStoreIds(new Set())
      setJob(null)
      setJobId(null)
      setExpandedStop(null)
      setFocusedStop(null)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Could not find nearby stores')
    } finally {
      setSearchingStores(false)
    }
  }

  const selectStoreMode = (mode: StoreMode) => {
    const changedMode = mode !== storeMode
    setStoreMode(mode)
    if (mode === 'selected' && changedMode) setSelectedStoreIds(new Set())
    else if (mode === 'all' && storePreview) {
      setSelectedStoreIds(new Set(storePreview.stores.filter((store) => store.selectable).map((store) => store.catalogId)))
    } else if (mode === 'optimized') setSelectedStoreIds(new Set())
    setError('')
  }

  const toggleStore = (storeId: string) => {
    if (result || storeMode !== 'selected') return
    setSelectedStoreIds((current) => {
      const next = new Set(current)
      if (next.has(storeId)) next.delete(storeId)
      else next.add(storeId)
      return next
    })
  }

  const generate = async () => {
    if (loading) return
    setError('')
    setJob(null)
    if (!/^\d{5}$/.test(homeZip)) {
      setError('Enter a five-digit starting ZIP code.')
      return
    }
    if (skus.length === 0 || skus.length > 25) {
      setError('Enter between 1 and 25 SKUs.')
      return
    }
    if (!bearerToken.trim()) {
      setError('Paste your Hidden Clearances bearer token.')
      return
    }
    if (!storePreview || !previewIsCurrent) {
      setError('Find nearby stores before generating the stock route.')
      return
    }
    if (storeMode === 'selected' && selectedStoreIds.size === 0) {
      setError('Select at least one store on the map.')
      return
    }
    setSubmitting(true)
    try {
      const response = await fetch('/api/plans', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          homeZip,
          skus,
          bearerToken: bearerToken.trim(),
          restockLimit,
          minimumDiscountPercent: 80,
          stockBuffer: 0,
          maxRadiusMiles,
          storeMode,
          selectedStoreIds: [...selectedStoreIds],
        }),
      })
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Could not start the route')
      }
      const created = await response.json() as { id: string }
      setJobId(created.id)
      setJob({ id: created.id, status: 'queued', progress: 0, message: 'Starting route', result: null, error: null })
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Could not start the route')
    } finally {
      setSubmitting(false)
    }
  }

  const refresh = async () => {
    if (!jobId || loading) return
    setError('')
    setSubmitting(true)
    try {
      const response = await fetch(`/api/plans/${jobId}/refresh`, { method: 'POST' })
      if (!response.ok) {
        setError('Could not refresh this route.')
        return
      }
      setJob((current) => current ? { ...current, status: 'queued', progress: 0, result: null } : current)
      setExpandedStop(null)
      setFocusedStop(null)
      setPollVersion((version) => version + 1)
    } finally {
      setSubmitting(false)
    }
  }

  const activateStop = (sequence: number) => {
    setFocusedStop(sequence)
    setExpandedStop((current) => current === sequence ? null : sequence)
  }

  const forgetSavedInputs = () => {
    try {
      window.localStorage.removeItem(SKU_STORAGE_KEY)
      window.localStorage.removeItem(TOKEN_STORAGE_KEY)
    } catch {
      // State is still cleared when local storage is unavailable.
    }
    setSkuText('')
    setBearerToken('')
  }

  const selectableStoreCount = storePreview?.stores.filter((store) => store.selectable).length ?? 0

  return (
    <div className="dark h-svh overflow-hidden bg-[#070b12] text-slate-100">
      <div className="grid h-full min-h-0 grid-cols-[300px_minmax(0,1fr)_360px]">
        <aside className="min-h-0 border-r border-slate-800/80 bg-[#0a0f18]">
          <ScrollArea className="h-full">
            <div className="px-4 py-5">
              <div className="mb-7 flex items-center gap-3">
                <div className="flex size-9 items-center justify-center rounded-lg border border-blue-400/20 bg-blue-500/10 text-blue-300">
                  <Route className="size-4.5" />
                </div>
                <div>
                  <h1 className="text-sm font-semibold tracking-tight text-white">StockPath</h1>
                  <p className="mt-0.5 text-[9px] font-medium uppercase tracking-[0.18em] text-slate-600">Home Depot route planner</p>
                </div>
              </div>

              <section className="border-b border-slate-800/80 pb-5">
                <SectionHeading number="01" title="Driving area" detail="Set the starting point and hard distance limit." />
                <div className="grid grid-cols-[1fr_112px] gap-2">
                  <label>
                    <span className="mb-1.5 block text-[11px] font-medium text-slate-400">Starting ZIP</span>
                    <div className="relative">
                      <MapPin className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-blue-400" />
                      <Input
                        value={homeZip}
                        disabled={loading || searchingStores}
                        onChange={(event) => {
                          setHomeZip(event.target.value.replace(/\D/g, '').slice(0, 5))
                          invalidateStorePreview()
                        }}
                        className="h-9 border-slate-700/80 bg-[#070b12] pl-9 text-sm"
                        inputMode="numeric"
                      />
                    </div>
                  </label>
                  <label>
                    <span className="mb-1.5 block text-[11px] font-medium text-slate-400">Radius</span>
                    <div className="relative">
                      <Input
                        type="number"
                        min={5}
                        max={150}
                        step={5}
                        value={maxRadiusMiles}
                        disabled={loading || searchingStores}
                        onChange={(event) => {
                          setMaxRadiusMiles(Math.min(150, Math.max(5, Number(event.target.value) || 5)))
                          invalidateStorePreview()
                        }}
                        className="h-9 border-slate-700/80 bg-[#070b12] pr-8 text-sm"
                      />
                      <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-slate-600">mi</span>
                    </div>
                  </label>
                </div>
                <Button
                  onClick={() => void findNearbyStores()}
                  disabled={loading || searchingStores}
                  variant="outline"
                  className="mt-3 h-9 w-full border-slate-700/80 bg-slate-900/50 text-xs text-slate-200 hover:border-blue-500/50 hover:bg-blue-500/10"
                >
                  {searchingStores ? <RefreshCw className="animate-spin" /> : <MapPin />}
                  {searchingStores ? 'Finding stores…' : 'Find stores in radius'}
                </Button>
              </section>

              <section className="border-b border-slate-800/80 py-5">
                <SectionHeading number="02" title="Route coverage" detail="Choose how stores enter the route." />
                {!storePreview || !previewIsCurrent ? (
                  <div className="border border-dashed border-slate-800 px-3 py-4 text-center text-[11px] leading-4 text-slate-600">
                    Find stores to choose route coverage.
                  </div>
                ) : (
                  <>
                    <div className="mb-3 flex items-center justify-between text-[11px]">
                      <span className="text-slate-500">{storePreview.stores.length} mapped · {maxRadiusMiles} mile radius</span>
                      <span className="font-medium tabular-nums text-blue-300">
                        {storeMode === 'selected' ? `${selectedStoreIds.size} selected` : storeMode === 'all' ? `${selectableStoreCount} required` : 'Auto'}
                      </span>
                    </div>
                    <div className="grid grid-cols-3 border border-slate-800">
                      {([
                        ['optimized', 'Optimize'],
                        ['selected', 'Select'],
                        ['all', 'All'],
                      ] as const).map(([mode, label], index) => (
                        <button
                          key={mode}
                          type="button"
                          onClick={() => selectStoreMode(mode)}
                          className={`h-9 text-[10px] font-semibold transition ${index > 0 ? 'border-l border-slate-800' : ''} ${
                            storeMode === mode ? 'bg-blue-500/15 text-blue-300' : 'bg-[#070b12] text-slate-500 hover:text-slate-300'
                          }`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <p className="mt-2 text-[10px] leading-4 text-slate-600">
                      {storeMode === 'optimized' && 'StockPath chooses the strongest nearby clusters.'}
                      {storeMode === 'selected' && 'Nothing is preselected. Click map points to add stores.'}
                      {storeMode === 'all' && 'Every supported store in the radius is required.'}
                    </p>
                    {storePreview.truncated && <p className="mt-1 text-[10px] text-amber-300/70">Showing the nearest 100 supported stores.</p>}
                  </>
                )}
              </section>

              <section className="border-b border-slate-800/80 py-5">
                <SectionHeading number="03" title="Inventory" detail="Up to 25 SKUs. Stock requests are paced across two minutes." />
                <label className="block">
                  <span className="mb-1.5 flex items-center justify-between text-[11px] font-medium text-slate-400">
                    <span>SKU list</span>
                    <span className={skus.length > 25 ? 'text-red-400' : 'tabular-nums text-slate-600'}>{skus.length}/25</span>
                  </span>
                  <textarea
                    value={skuText}
                    onChange={(event) => setSkuText(event.target.value)}
                    placeholder={'206751547\n315702244\n330529510'}
                    className="h-32 w-full resize-none rounded-lg border border-slate-700/80 bg-[#070b12] px-3 py-2 font-mono text-xs leading-6 text-slate-200 outline-none placeholder:text-slate-700 focus:border-blue-500/70 focus:ring-2 focus:ring-blue-500/10"
                  />
                </label>
                <label className="mt-3 block">
                  <span className="mb-1.5 block text-[11px] font-medium text-slate-400">Hidden Clearances token</span>
                  <div className="relative">
                    <KeyRound className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-blue-400" />
                    <Input
                      type="password"
                      value={bearerToken}
                      onChange={(event) => setBearerToken(event.target.value)}
                      className="h-9 border-slate-700/80 bg-[#070b12] pl-9 font-mono text-xs"
                      placeholder="Paste bearer token"
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </div>
                  <p className="mt-1 text-[9px] text-slate-700">Stored only in this browser.</p>
                </label>
              </section>

              <section className="py-5">
                <SectionHeading number="04" title="Receipt rule" detail="Maximum units on one price adjustment receipt." />
                <div className="grid grid-cols-3 border border-slate-800">
                  {[6, 7, 8].map((limit, index) => (
                    <button
                      key={limit}
                      type="button"
                      onClick={() => setRestockLimit(limit)}
                      className={`h-9 text-xs font-semibold transition ${index > 0 ? 'border-l border-slate-800' : ''} ${
                        restockLimit === limit ? 'bg-blue-500/15 text-blue-300' : 'bg-[#070b12] text-slate-500 hover:text-slate-300'
                      }`}
                    >
                      {limit} units
                    </button>
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-between border-l-2 border-cyan-500/60 pl-3 text-[10px]">
                  <span className="text-slate-600">Clearance threshold</span>
                  <span className="font-semibold text-cyan-300">80% off or better</span>
                </div>
              </section>

              <Button
                onClick={() => void generate()}
                disabled={loading || searchingStores || !previewIsCurrent}
                className="h-10 w-full bg-blue-600 font-semibold text-white shadow-lg shadow-blue-950/20 hover:bg-blue-500"
              >
                {loading ? <RefreshCw className="animate-spin" /> : <Search />}
                {loading ? 'Building route…' : 'Generate route'}
              </Button>

              {loading && (
                <div className="mt-3 border border-slate-800 bg-slate-900/40 p-3">
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="truncate text-slate-500">{job?.message}</span>
                    <span className="font-semibold tabular-nums text-blue-300">{job?.progress ?? 0}%</span>
                  </div>
                  <div className="mt-2 h-1 overflow-hidden bg-slate-800">
                    <div className="h-full bg-blue-500 transition-all" style={{ width: `${job?.progress ?? 0}%` }} />
                  </div>
                </div>
              )}

              {error && <div className="mt-3 border-l-2 border-red-500 bg-red-500/[0.06] px-3 py-2.5 text-[11px] leading-4 text-red-300">{error}</div>}

              <div className="mt-5 border-t border-slate-800/80 pt-4">
                <div className="flex items-start gap-2 text-[10px] leading-4 text-slate-600">
                  <ReceiptText className="mt-0.5 size-3.5 shrink-0" />
                  One earlier receipt per price-match stop. Mixed SKUs are grouped automatically.
                </div>
                {(skuText || bearerToken) && (
                  <button
                    type="button"
                    onClick={forgetSavedInputs}
                    className="mt-3 text-[9px] text-slate-700 underline decoration-slate-800 underline-offset-4 transition hover:text-red-300"
                  >
                    Forget saved SKUs and token
                  </button>
                )}
              </div>
            </div>
          </ScrollArea>
        </aside>

        <main className="relative min-h-0 min-w-0 bg-[#080d15]">
          <RouteMap
            result={result}
            preview={storePreview}
            storeMode={storeMode}
            focusedStop={focusedStop}
            selectedStoreIds={selectedStoreIds}
            onToggleStore={toggleStore}
            onFocusStop={(sequence) => {
              setFocusedStop(sequence)
              setExpandedStop(sequence)
            }}
          />

          {!result && !loading && !storePreview && (
            <div className="pointer-events-none absolute inset-x-0 top-5 z-[500] mx-auto w-fit rounded-full border border-slate-700/80 bg-[#080d15]/90 px-4 py-2 text-[11px] text-slate-400 shadow-xl backdrop-blur">
              Find nearby stores to begin
            </div>
          )}
          {!result && storePreview && (
            <div className="pointer-events-none absolute inset-x-0 top-5 z-[500] mx-auto w-fit rounded-full border border-slate-700/80 bg-[#080d15]/90 px-4 py-2 text-[11px] text-slate-300 shadow-xl backdrop-blur">
              {storeMode === 'selected'
                ? `${selectedStoreIds.size} selected · click a store point to add or remove`
                : storeMode === 'all'
                  ? `${selectableStoreCount} stores required`
                  : 'Cluster optimization will choose the route stops'}
            </div>
          )}
          {result && (
            <>
              <div className="absolute left-4 top-4 z-[500] flex items-center divide-x divide-slate-700/80 rounded-lg border border-slate-700/80 bg-[#080d15]/90 shadow-xl backdrop-blur">
                <div className="px-3 py-2">
                  <p className="text-[8px] font-semibold uppercase tracking-[0.14em] text-slate-600">Route</p>
                  <p className="mt-0.5 text-sm font-semibold tabular-nums text-slate-100">{result.summary.totalMiles} <span className="text-[10px] font-normal text-slate-500">mi</span></p>
                </div>
                <div className="px-3 py-2">
                  <p className="text-[8px] font-semibold uppercase tracking-[0.14em] text-slate-600">Stops</p>
                  <p className="mt-0.5 text-sm font-semibold tabular-nums text-slate-100">{result.summary.storesVisited}</p>
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setFocusedStop(null)}
                className="absolute right-4 top-4 z-[500] border-slate-700/80 bg-[#080d15]/90 text-slate-200 shadow-xl backdrop-blur hover:border-slate-600 hover:bg-[#0d1624] hover:text-white"
              >
                <Crosshair /> Fit route
              </Button>
            </>
          )}
        </main>

        <aside className="min-h-0 border-l border-slate-800/80 bg-[#0a0f18]">
          {!result ? (
            <div className="flex h-full flex-col items-center justify-center px-8 text-center">
              <div className="flex size-11 items-center justify-center rounded-full border border-slate-800 bg-slate-900/50 text-slate-600">
                <Truck className="size-4.5" />
              </div>
              <p className="mt-4 text-sm font-medium text-slate-300">No route yet</p>
              <p className="mt-1 max-w-56 text-[11px] leading-5 text-slate-600">
                {storePreview
                  ? 'Set the coverage mode, then generate the route to organize stores and receipts.'
                  : 'Find nearby stores to preview your driving area before loading stock.'}
              </p>
            </div>
          ) : (
            <div className="flex h-full min-h-0 flex-col">
              <div className="shrink-0 border-b border-slate-800/80 px-4 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[9px] font-semibold uppercase tracking-[0.16em] text-blue-400">Route manifest</p>
                    <h2 className="mt-1 text-base font-semibold text-white">Store checklist</h2>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void refresh()}
                    disabled={loading}
                    className="text-[10px] text-slate-500 hover:text-blue-300"
                  >
                    <RefreshCw className={loading ? 'animate-spin' : ''} /> Refresh
                  </Button>
                </div>
                <div className="mt-4 grid grid-cols-3 divide-x divide-slate-800">
                  <div className="pr-3">
                    <p className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.1em] text-slate-600"><StoreIcon className="size-3 text-blue-400" /> Stores</p>
                    <p className="mt-1 text-lg font-semibold tabular-nums text-slate-100">{result.summary.storesVisited}</p>
                  </div>
                  <div className="px-3">
                    <p className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.1em] text-slate-600"><ShoppingCart className="size-3 text-blue-400" /> Units</p>
                    <p className="mt-1 text-lg font-semibold tabular-nums text-slate-100">{result.summary.totalUnits}</p>
                  </div>
                  <div className="pl-3">
                    <p className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.1em] text-slate-600"><CircleDollarSign className="size-3 text-cyan-400" /> Savings</p>
                    <p className="mt-1 truncate text-lg font-semibold tabular-nums text-cyan-300">{formatMoney(result.summary.totalSavings)}</p>
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-1.5 text-[9px] text-slate-600">
                  <Check className="size-3 text-cyan-400" /> Route is read-only. Click a stop to focus it on the map.
                </div>
              </div>

              <ScrollArea className="min-h-0 flex-1">
                <div>
                  {result.stops.map((stop) => (
                    <StopCard
                      key={stop.storeId}
                      stop={stop}
                      expanded={expandedStop === stop.sequence}
                      focused={focusedStop === stop.sequence}
                      onActivate={() => activateStop(stop.sequence)}
                    />
                  ))}
                  <div className="flex items-center gap-3 border-b border-slate-800/80 px-4 py-4 text-xs text-slate-500">
                    <span className="flex size-6 items-center justify-center rounded-full border border-slate-700"><ArrowRight className="size-3" /></span>
                    <span className="flex-1">Return to {result.homeZip}</span>
                    <span className="font-semibold tabular-nums text-slate-300">{result.summary.returnMiles} mi</span>
                  </div>
                  {result.warnings.length > 0 && (
                    <div className="border-b border-amber-500/15 bg-amber-500/[0.04] px-4 py-3 text-[10px] leading-4 text-amber-200/70">
                      {result.warnings.map((warning) => <p key={warning}>{warning}</p>)}
                    </div>
                  )}
                </div>
              </ScrollArea>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

export default App
