export function number(value: unknown): string {
  return Number(value || 0).toLocaleString();
}

export function currency(value: unknown): string {
  return Number(value || 0).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export function shortName(name: string): string {
  return name.replace("Distribution Center", "DC").replace("Logistics", "Logistics");
}
