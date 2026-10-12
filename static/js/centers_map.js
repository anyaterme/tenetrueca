(() => {
  const root = document.querySelector('[data-centers-map]');
  const configElement = document.getElementById('google-maps-config');
  if (!root || !configElement) return;
  const config = JSON.parse(configElement.textContent);
  const loadButton = root.querySelector('[data-map-load]');
  if (!loadButton || !config.apiKey || !config.mapId) return;
  const canvas = root.querySelector('[data-map-canvas]');
  const intro = root.querySelector('[data-map-intro]');
  const status = root.querySelector('[data-map-status]');
  const editor = root.dataset.mapMode === 'editor';
  let map;
  let infoWindow;
  let initialized = false;
  let failed = false;
  const markers = new Map();
  const data = editor ? [] : JSON.parse(document.getElementById('centers-map-data').textContent);

  const fail = () => {
    failed = true;
    canvas.hidden = true;
    intro.hidden = false;
    loadButton.hidden = true;
    status.textContent = editor
      ? 'El mapa no está disponible. Puedes configurar la ubicación escribiendo las coordenadas.'
      : 'El mapa no está disponible. Puedes consultar el listado y utilizar los enlaces «Cómo llegar».';
    root.querySelectorAll('[data-center-select]').forEach((button) => { button.hidden = true; });
  };

  const loadGoogle = () => new Promise((resolve, reject) => {
    if (window.google?.maps?.Map) { resolve(); return; }
    const script = document.createElement('script');
    const timer = window.setTimeout(() => reject(new Error('Map loading timed out')), 20000);
    window.truecaMapsReady = () => { window.clearTimeout(timer); resolve(); };
    window.gm_authFailure = () => {
      window.clearTimeout(timer);
      fail();
      reject(new Error('Map authorization failed'));
    };
    script.src = 'https://maps.googleapis.com/maps/api/js?' + new URLSearchParams({
      key: config.apiKey, callback: 'truecaMapsReady', loading: 'async',
      libraries: 'maps,marker', v: 'quarterly', language: 'es', region: 'ES',
      auth_referrer_policy: 'origin',
    });
    script.async = true;
    script.onerror = () => { window.clearTimeout(timer); reject(new Error('Map loading failed')); };
    document.head.append(script);
  });

  const infoContent = (center) => {
    const content = document.createElement('div');
    content.className = 'center-map-info';
    const heading = document.createElement('h3');
    heading.textContent = center.name;
    content.append(heading);
    [center.status, center.address, center.schedule, center.phone, center.information].filter(Boolean).forEach((text) => {
      const paragraph = document.createElement('p');
      paragraph.textContent = text;
      content.append(paragraph);
    });
    const link = document.createElement('a');
    link.href = center.directionsUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = 'Cómo llegar ↗';
    link.setAttribute('aria-label', `Cómo llegar a ${center.name} en Google Maps (nueva pestaña)`);
    content.append(link);
    return content;
  };

  const selectCenter = (id, fromList = false) => {
    const center = data.find((item) => String(item.id) === String(id));
    if (!center || !initialized || failed) return;
    map.panTo({ lat: center.lat, lng: center.lng });
    map.setZoom(15);
    infoWindow.setContent(infoContent(center));
    infoWindow.open({ map, anchor: markers.get(center.id), shouldFocus: fromList });
    root.querySelectorAll('[data-center-card]').forEach((card) => {
      const selected = card.dataset.centerCard === String(id);
      card.classList.toggle('is-selected', selected);
      if (selected && !fromList) card.scrollIntoView({ block: 'nearest', behavior: 'instant' });
    });
    if (fromList) canvas.scrollIntoView({ block: 'nearest', behavior: 'instant' });
    status.textContent = `Ubicación de ${center.name}.`;
  };

  const setupPublicMap = () => {
    const bounds = new google.maps.LatLngBounds();
    infoWindow = new google.maps.InfoWindow();
    data.forEach((center) => {
      const position = { lat: center.lat, lng: center.lng };
      const marker = new google.maps.marker.AdvancedMarkerElement({
        map, position, title: center.name, gmpClickable: true,
      });
      marker.addEventListener('gmp-click', () => selectCenter(center.id));
      markers.set(center.id, marker);
      bounds.extend(position);
    });
    if (data.length === 1) { map.setCenter(bounds.getCenter()); map.setZoom(14); }
    else if (data.length) map.fitBounds(bounds, 45);
    root.querySelectorAll('[data-center-select]').forEach((button) => {
      button.hidden = false;
      button.addEventListener('click', () => selectCenter(button.dataset.centerSelect, true));
    });
    status.textContent = `${data.length} puntos limpios en el mapa. Selecciona un marcador o un centro del listado.`;
  };

  const setupEditor = () => {
    const latitude = document.getElementById('id_latitude');
    const longitude = document.getElementById('id_longitude');
    const readPosition = () => {
      if (!latitude.value.trim() || !longitude.value.trim()) return null;
      const lat = Number(latitude.value);
      const lng = Number(longitude.value);
      return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? { lat, lng } : null;
    };
    const marker = new google.maps.marker.AdvancedMarkerElement({
      map, position: readPosition(), title: 'Ubicación del punto limpio', gmpDraggable: true,
    });
    const setPosition = (position) => {
      if (!position) return;
      latitude.value = position.lat.toFixed(6);
      longitude.value = position.lng.toFixed(6);
      marker.position = position;
      status.textContent = 'Ubicación seleccionada. Guarda el formulario para aplicar los cambios.';
    };
    map.addListener('click', (event) => setPosition(event.latLng?.toJSON()));
    marker.addListener('dragend', () => {
      const position = marker.position;
      setPosition(typeof position?.toJSON === 'function' ? position.toJSON() : position);
    });
    const updateFromFields = () => {
      const position = readPosition();
      marker.position = position;
      if (position) { map.panTo(position); map.setZoom(16); }
    };
    latitude.addEventListener('change', updateFromFields);
    longitude.addEventListener('change', updateFromFields);
    updateFromFields();
    status.textContent = 'Haz clic en el mapa o arrastra el marcador para seleccionar la ubicación.';
  };

  loadButton.addEventListener('click', async () => {
    loadButton.disabled = true;
    root.setAttribute('aria-busy', 'true');
    status.textContent = 'Cargando Google Maps…';
    try {
      await loadGoogle();
      if (failed) return;
      canvas.hidden = false;
      map = new google.maps.Map(canvas, {
        center: { lat: 28.3, lng: -16.55 }, zoom: 10, mapId: config.mapId,
        streetViewControl: false, mapTypeControl: false, gestureHandling: 'cooperative',
      });
      initialized = true;
      if (editor) setupEditor(); else setupPublicMap();
      intro.hidden = true;
      canvas.tabIndex = -1;
      canvas.focus({ preventScroll: true });
    } catch { fail(); }
    finally { root.removeAttribute('aria-busy'); }
  });
})();
