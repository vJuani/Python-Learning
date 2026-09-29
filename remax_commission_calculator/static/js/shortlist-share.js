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
    var toast = document.getElementById("shortlist-share-toast");
    var toastText = toast ? toast.querySelector("[data-share-toast-text]") : null;
    var copyButton = toast ? toast.querySelector("[data-share-copy]") : null;
    var cache = {};
    var pendingText = "";

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

    function whatsappUrl(item, text) {
        if (!item.whatsapp_url || text === item.text) {
            return item.whatsapp_url || "";
        }
        try {
            var url = new URL(item.whatsapp_url);
            url.searchParams.set("text", text);
            return url.toString();
        } catch (error) {
            return item.whatsapp_url;
        }
    }

    function shareText(item) {
        var box = document.getElementById("shortlist-message");
        if (box && items.length === 1) {
            return box.value || item.text || "";
        }
        return item.text || "";
    }

    function copyText(text) {
        pendingText = text || "";
        if (!navigator.clipboard || !navigator.clipboard.writeText) {
            return Promise.resolve(false);
        }
        return navigator.clipboard.writeText(pendingText).then(function () {
            return true;
        }).catch(function () {
            return false;
        });
    }

    function showToast(copied) {
        if (!toast || !toastText) {
            return;
        }
        toastText.textContent = copied
            ? (root.getAttribute("data-copied") || "")
            : (root.getAttribute("data-copy-miss") || "");
        if (copyButton) {
            copyButton.hidden = !!copied;
        }
        toast.hidden = false;
    }

    function showDownloadNotice() {
        if (notice) {
            notice.hidden = false;
        }
    }

    function fallback(item, file, text) {
        if (file) {
            downloadFile(file);
        }
        var target = whatsappUrl(item, text);
        if (target) {
            window.open(target, "_blank", "noopener");
        }
        showDownloadNotice();
    }

    function shareFile(file, item, text, copied) {
        var shareData = {
            files: [file],
            title: item.title || "",
            text: text
        };
        if (!(navigator.canShare && navigator.canShare(shareData))) {
            fallback(item, file, text);
            return;
        }
        showToast(copied);
        return navigator.share(shareData).catch(function (error) {
            if (error && error.name === "AbortError") {
                return;
            }
            fallback(item, file, text);
        });
    }

    if (copyButton) {
        copyButton.addEventListener("click", function () {
            copyText(pendingText).then(function (copied) {
                if (copied) {
                    showToast(true);
                }
            });
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
            var text = shareText(item);
            var copyPromise = copyText(text);
            button.disabled = true;
            loadPdf(item).then(function (file) {
                return copyPromise.then(function (copied) {
                    return shareFile(file, item, text, copied);
                });
            }).catch(function () {
                delete cache[String(item.property_id)];
            }).then(function () {
                button.disabled = false;
            });
        });
    });
})();
