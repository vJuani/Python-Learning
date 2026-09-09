(function () {
    function componentName(components, typeName) {
        var i;
        for (i = 0; i < (components || []).length; i += 1) {
            if ((components[i].types || []).indexOf(typeName) !== -1) {
                return components[i].long_name || components[i].short_name || "";
            }
        }
        return "";
    }

    function fill(root, selector, value) {
        var field = root.querySelector(selector);
        if (field) {
            field.value = value || "";
        }
    }

    function clearPlace(root) {
        fill(root, "[data-jrh-place-id]", "");
        fill(root, "[data-jrh-formatted]", "");
        fill(root, "[data-jrh-locality]", "");
        fill(root, "[data-jrh-admin-area]", "");
        fill(root, "[data-jrh-country]", "");
        fill(root, "[data-jrh-postal]", "");
        fill(root, "[data-jrh-lat]", "");
        fill(root, "[data-jrh-lng]", "");
    }

    function normalizeKey(value) {
        return String(value || "")
            .toLowerCase()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .replace(/[^a-z0-9]+/g, "");
    }

    function initAutocomplete(root) {
        var input = root.querySelector("[data-jrh-address]");
        if (!input || !window.google || !google.maps || !google.maps.places) {
            return;
        }
        var original = normalizeKey(input.value);
        var autocomplete = new google.maps.places.Autocomplete(input, {
            fields: ["address_components", "formatted_address", "geometry", "place_id"],
            types: ["address"],
        });
        autocomplete.addListener("place_changed", function () {
            var place = autocomplete.getPlace() || {};
            var location = place.geometry && place.geometry.location;
            if (!place.place_id || !location) {
                return;
            }
            var components = place.address_components || [];
            var route = componentName(components, "route");
            var number = componentName(components, "street_number");
            var street = [route, number].filter(Boolean).join(" ").trim();
            if (street) {
                input.value = street;
            }
            fill(root, "[data-jrh-place-id]", place.place_id);
            fill(root, "[data-jrh-formatted]", place.formatted_address || "");
            fill(root, "[data-jrh-locality]", componentName(components, "locality"));
            fill(root, "[data-jrh-admin-area]", componentName(components, "administrative_area_level_1"));
            fill(root, "[data-jrh-country]", componentName(components, "country"));
            fill(root, "[data-jrh-postal]", componentName(components, "postal_code"));
            fill(root, "[data-jrh-lat]", String(location.lat()));
            fill(root, "[data-jrh-lng]", String(location.lng()));
            var neighborhood = document.getElementById("neighborhood");
            var zone = componentName(components, "neighborhood")
                || componentName(components, "sublocality_level_1")
                || componentName(components, "sublocality");
            if (neighborhood && zone && !neighborhood.value) {
                neighborhood.value = zone;
            }
            original = normalizeKey(input.value);
        });
        input.addEventListener("input", function () {
            if (normalizeKey(input.value) !== original) {
                clearPlace(root);
                original = "";
            }
        });
    }

    function initEmbed(root) {
        var lat = parseFloat(root.getAttribute("data-maps-lat"));
        var lng = parseFloat(root.getAttribute("data-maps-lng"));
        if (!window.google || !google.maps || Number.isNaN(lat) || Number.isNaN(lng)) {
            return;
        }
        var map = new google.maps.Map(root, {
            center: { lat: lat, lng: lng },
            zoom: 16,
            disableDefaultUI: true,
            zoomControl: true,
            fullscreenControl: true,
        });
        new google.maps.Marker({
            map: map,
            position: { lat: lat, lng: lng },
            title: root.getAttribute("data-maps-title") || "",
        });
    }

    function initCluster(root) {
        var raw = root.getAttribute("data-maps-payload") || "";
        var payload;
        try {
            payload = JSON.parse(raw);
        } catch (err) {
            return;
        }
        var target = payload && payload.target;
        if (!window.google || !google.maps || !target) {
            return;
        }
        var map = new google.maps.Map(root, {
            center: { lat: Number(target.lat), lng: Number(target.lng) },
            zoom: 14,
            disableDefaultUI: true,
            zoomControl: true,
            fullscreenControl: true,
        });
        var bounds = new google.maps.LatLngBounds();
        var targetPos = { lat: Number(target.lat), lng: Number(target.lng) };
        new google.maps.Marker({
            map: map,
            position: targetPos,
            title: target.title || "",
            zIndex: 1000,
        });
        bounds.extend(targetPos);
        (payload.markers || []).forEach(function (item) {
            var pos = { lat: Number(item.lat), lng: Number(item.lng) };
            if (Number.isNaN(pos.lat) || Number.isNaN(pos.lng)) {
                return;
            }
            var marker = new google.maps.Marker({
                map: map,
                position: pos,
                title: item.title || "",
                opacity: 0.85,
            });
            bounds.extend(pos);
            marker.addListener("click", function () {
                var info = new google.maps.InfoWindow({
                    content: "<strong>" + (item.title || "") + "</strong><br>"
                        + (item.distance || "") + " "
                        + (item.price || "")
                        + (item.href ? "<br><a href=\"" + item.href + "\">Ver</a>" : ""),
                });
                info.open(map, marker);
            });
        });
        if (!bounds.isEmpty()) {
            map.fitBounds(bounds, 48);
        }
    }

    window.jrhInitPropertyMaps = function () {
        document.querySelectorAll("[data-jrh-maps]").forEach(function (root) {
            var mode = root.getAttribute("data-maps-mode");
            if (mode === "autocomplete") {
                initAutocomplete(root);
            }
            if (mode === "embed") {
                initEmbed(root);
            }
            if (mode === "cluster") {
                initCluster(root);
            }
        });
    };

    var host = document.querySelector("[data-jrh-maps]");
    if (!host) {
        return;
    }
    var key = host.getAttribute("data-maps-key") || "";
    if (!key) {
        return;
    }
    var region = host.getAttribute("data-maps-region") || "ar";
    var script = document.createElement("script");
    script.src = "https://maps.googleapis.com/maps/api/js?key="
        + encodeURIComponent(key)
        + "&libraries=places&region=" + encodeURIComponent(region)
        + "&callback=jrhInitPropertyMaps";
    script.async = true;
    document.head.appendChild(script);
})();
