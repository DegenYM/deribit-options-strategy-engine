export function detailsSectionOpen(id) {
  return Boolean(document.getElementById(id)?.open);
}

export function strategiesSectionOpen() {
  return detailsSectionOpen("strategies-section");
}

export function accountSectionOpen() {
  return detailsSectionOpen("account-section");
}

export function booksSectionOpen() {
  return detailsSectionOpen("books-section");
}
