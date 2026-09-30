(function () {
    var root = document.querySelector("[data-shortlist-share]");
    var payloadNode = document.getElementById("shortlist-share-data");
    if (!root || !payloadNode) {
        return;
    }

    var parsed = JSON.parse(payloadNode.textContent);
    var items = Array.isArray(parsed) ? parsed : (parsed.items || []);
    var byId = {};
    items.forEach(function (item) {
        byId[String(item.property_id)] = item;
    });

    var notice = document.getElementById("shortlist-share-notice");
    var cache = {};

    function filenameFrom(response) {
        var header = response.headers.get("Content-Disposition") || "";
        var star = /filename\*=UTF-8''([^;]+)/i.exec(header);
        if (star) {
            try {
                return decodeURIComponent(star[1].trim());
            } catch (error) {
                return star[1].trim();
            }
        }
        var plain = /filename="([^"]+)"|filename=([^;]+)/i.exec(header);
        if (!plain) {
            return "Ficha.pdf";
        }
        return (plain[1] || plain[2] || "Ficha.pdf").trim();
    }

    function loadPdf(item) {
        var key = String(item.property_id);
        if (!cache[key]) {
            cache[key] = fetch(item.pdf_url, { credentials: "same-origin" }).then(function (response) {
                if (!response.ok) {
                    throw new Error("pdf");
                }
                return response.blob().then(function (blob) {
                    return new File([blob], filenameFrom(response), { type: "application/pdf" });
                });
            });
        }
        return cache[key];
    }

    function downloadFile(file) {
        var url = URL.createObjectURL(file);
        var link = document.createElement("a");
        link.href = url;
        link.download = file.name || "Ficha.pdf";
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(function () {
            URL.revokeObjectURL(url);
        }, 1500);
    }

    function shareFile(file, item) {
        var shareData = {
            files: [file],
            title: item.title || ""
        };
        if (!(navigator.canShare && navigator.canShare(shareData))) {
            downloadFile(file);
            if (notice) {
                notice.hidden = false;
            }
            return;
        }
        return navigator.share(shareData).catch(function (error) {
            if (error && error.name === "AbortError") {
                return;
            }
            downloadFile(file);
            if (notice) {
                notice.hidden = false;
            }
        });
    }

    root.querySelectorAll("[data-share-sheet]").forEach(function (button) {
        button.addEventListener("pointerdown", function () {
            var item = byId[button.getAttribute("data-property-id")];
            if (!item) {
                return;
            }
            loadPdf(item).catch(function () {
                delete cache[String(item.property_id)];
            });
        });

        button.addEventListener("click", function () {
            var item = byId[button.getAttribute("data-property-id")];
            if (!item || button.disabled) {
                return;
            }
            button.disabled = true;
            loadPdf(item).then(function (file) {
                return shareFile(file, item);
            }).catch(function () {
                delete cache[String(item.property_id)];
            }).then(function () {
                button.disabled = false;
            });
        });
    });
})();
