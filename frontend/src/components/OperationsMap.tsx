import { useEffect, useRef } from "react";
import L, { type LayerGroup, type Map as LeafletMap } from "leaflet";
import type { DemoState, Restaurant, Route } from "../types";
import { number, shortName } from "../utils";

const statusColors: Record<string, string> = {
  healthy: "#22c55e",
  low: "#f59e0b",
  critical: "#ef4444",
  expiry: "#a855f7",
};

type Props = {
  state: DemoState;
};

export function OperationsMap({ state }: Props) {
  const mapRef = useRef<LeafletMap | null>(null);
  const restaurantLayer = useRef<LayerGroup | null>(null);
  const warehouseLayer = useRef<LayerGroup | null>(null);
  const routeLayer = useRef<LayerGroup | null>(null);

  useEffect(() => {
    if (mapRef.current) return;

    const map = L.map("map", {
      zoomControl: false,
      attributionControl: false,
    }).setView([40.735, -73.985], 11);

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 19,
    }).addTo(map);

    restaurantLayer.current = L.layerGroup().addTo(map);
    warehouseLayer.current = L.layerGroup().addTo(map);
    routeLayer.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !restaurantLayer.current || !warehouseLayer.current || !routeLayer.current) return;

    restaurantLayer.current.clearLayers();
    warehouseLayer.current.clearLayers();
    routeLayer.current.clearLayers();

    const bounds: Array<[number, number]> = [];

    state.restaurants.forEach((store) => {
      const color = statusColors[store.status] || statusColors.healthy;
      const height = Math.max(22, Math.min(96, Math.round(store.inventory_units / 12)));
      const marker = L.marker([store.lat, store.lng], {
        icon: L.divIcon({
          className: "store-tower-icon",
          html: `
            <div class="tower-wrap">
              <div class="tower" style="height:${height}px;border-color:${color};box-shadow:0 0 22px ${color}66">
                <span style="background:${color}"></span>
              </div>
              <b>${escapeHtml(shortName(store.name))}</b>
            </div>
          `,
          iconSize: [72, 112],
          iconAnchor: [36, 96],
        }),
      }).addTo(restaurantLayer.current!);
      marker.bindPopup(storePopup(store));
      bounds.push([store.lat, store.lng]);
    });

    state.warehouses.forEach((warehouse) => {
      L.marker([warehouse.lat, warehouse.lng], {
        icon: L.divIcon({
          className: "warehouse-icon",
          html: `
            <div class="warehouse-node">
              <div></div>
              <strong>${escapeHtml(shortName(warehouse.name))}</strong>
            </div>
          `,
          iconSize: [86, 72],
          iconAnchor: [43, 54],
        }),
      })
        .addTo(warehouseLayer.current!)
        .bindPopup(`<h3>${escapeHtml(warehouse.name)}</h3><p>${number(warehouse.inventory_units)} supply units</p>`);
      bounds.push([warehouse.lat, warehouse.lng]);
    });

    state.routes.forEach((route) => drawRoute(route, routeLayer.current!));

    if (bounds.length) {
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 11 });
    }
  }, [state]);

  return <div id="map" />;
}

function drawRoute(route: Route, layer: LayerGroup) {
  const color = route.type === "transfer" ? "#38bdf8" : "#f59e0b";
  L.polyline(
    [
      [route.from.lat, route.from.lng],
      [route.to.lat, route.to.lng],
    ],
    { color, weight: 4, opacity: 0.9, dashArray: "8 10", className: "animated-route" },
  )
    .addTo(layer)
    .bindPopup(`
      <h3>${escapeHtml(route.type)} proposal</h3>
      <p>${number(route.quantity)} units of ${escapeHtml(route.item_name)}</p>
      <p>${escapeHtml(route.from.name)} to ${escapeHtml(route.to.name)}</p>
    `);
}

function storePopup(store: Restaurant) {
  const riskPct = Math.round(store.stockout_risk * 100);
  const expiryPct = Math.round(store.expiry_risk * 100);
  const rows = store.top_items
    .slice(0, 5)
    .map(
      (item) => `
        <li>
          <span>${escapeHtml(item.name)}</span>
          <strong>${number(item.quantity)}</strong>
          <em>${Math.round(item.risk * 100)}% risk</em>
        </li>
      `,
    )
    .join("");
  return `
    <div class="popup-content">
      <h3>${escapeHtml(store.name)}</h3>
      <p>${number(store.inventory_units)} total units</p>
      <p>Stockout risk: ${riskPct}% | Expiry risk: ${expiryPct}%</p>
      <ul>${rows}</ul>
    </div>
  `;
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
