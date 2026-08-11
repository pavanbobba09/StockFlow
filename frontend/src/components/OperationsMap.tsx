import { useEffect, useRef } from "react";
import L, { type LayerGroup, type Map as LeafletMap } from "leaflet";
import type { DemoState, Restaurant, Route } from "../types";
import { number, shortName } from "../utils";

const statusColors: Record<string, string> = {
  healthy: "#2f855a",
  low: "#d97706",
  critical: "#dc2626",
  expiry: "#7c3aed",
};

export function OperationsMap({ state }: { state: DemoState }) {
  const mapRef = useRef<LeafletMap | null>(null);
  const restaurantLayer = useRef<LayerGroup | null>(null);
  const warehouseLayer = useRef<LayerGroup | null>(null);
  const routeLayer = useRef<LayerGroup | null>(null);
  const fittedBounds = useRef(false);

  useEffect(() => {
    if (mapRef.current) return;
    const map = L.map("map", { zoomControl: true, attributionControl: false }).setView([40.735, -73.985], 11);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
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
      fittedBounds.current = false;
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
      const marker = L.circleMarker([store.lat, store.lng], {
        radius: store.status === "critical" ? 9 : 7,
        color: "#ffffff",
        weight: 3,
        fillColor: color,
        fillOpacity: 0.96,
        className: `store-marker ${store.status}`,
      })
        .addTo(restaurantLayer.current!)
        .bindTooltip(store.name, { direction: "top", offset: [0, -8] })
        .bindPopup(storePopup(store));
      marker.on("mouseover", () => marker.openTooltip());
      bounds.push([store.lat, store.lng]);
    });

    state.warehouses.forEach((warehouse) => {
      L.marker([warehouse.lat, warehouse.lng], {
        icon: L.divIcon({
          className: "warehouse-icon",
          html: `<div class="warehouse-node"><span>W</span><strong>${escapeHtml(shortName(warehouse.name))}</strong></div>`,
          iconSize: [96, 54],
          iconAnchor: [48, 27],
        }),
      })
        .addTo(warehouseLayer.current!)
        .bindPopup(`<h3>${escapeHtml(warehouse.name)}</h3><p>${number(warehouse.inventory_units)} supply units</p>`);
      bounds.push([warehouse.lat, warehouse.lng]);
    });

    state.routes.slice(0, 8).forEach((route) => drawRoute(route, routeLayer.current!));
    if (bounds.length && !fittedBounds.current) {
      map.fitBounds(bounds, { padding: [44, 44], maxZoom: 11 });
      fittedBounds.current = true;
    }
  }, [state]);

  return <div id="map" aria-label="Map of stores, warehouses, and proposed inventory routes" />;
}

function drawRoute(route: Route, layer: LayerGroup) {
  const color = route.type === "transfer" ? "#2563eb" : "#d97706";
  L.polyline(
    [[route.from.lat, route.from.lng], [route.to.lat, route.to.lng]],
    { color, weight: 3, opacity: 0.75, dashArray: "7 9" },
  )
    .addTo(layer)
    .bindPopup(`
      <h3>${escapeHtml(route.type)} proposal</h3>
      <p>${number(route.quantity)} units of ${escapeHtml(route.item_name)}</p>
      <p>${escapeHtml(route.from.name)} to ${escapeHtml(route.to.name)}</p>
    `);
}

function storePopup(store: Restaurant) {
  const rows = store.top_items.slice(0, 4).map((item) => `
    <li><span>${escapeHtml(item.name)}</span><strong>${number(item.quantity)}</strong><em>${Math.round(item.risk * 100)}% risk</em></li>
  `).join("");
  return `
    <div class="popup-content">
      <h3>${escapeHtml(store.name)}</h3>
      <p>${number(store.inventory_units)} units · ${Math.round(store.stockout_risk * 100)}% stockout risk</p>
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
