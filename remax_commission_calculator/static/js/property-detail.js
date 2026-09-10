(function () {
    var root = document.querySelector("[data-pd-page]");
    if (!root) {
        return;
    }

    function qs(sel, el) {
        return (el || root).querySelector(sel);
    }

    function qsa(sel, el) {
        return Array.prototype.slice.call((el || root).querySelectorAll(sel));
    }

    var gallery = qs("[data-pd-gallery]");
    var sources = gallery
        ? qsa("[data-pd-src]", gallery).map(function (node) {
              return node.getAttribute("data-pd-src");
          }).filter(Boolean)
        : [];
    var hero = qs("[data-pd-hero]", gallery);
    var counter = qs("[data-pd-counter]", gallery);
    var index = 0;

    function setIndex(next) {
        if (!sources.length) {
            return;
        }
        index = (next + sources.length) % sources.length;
        if (hero) {
            hero.setAttribute("src", sources[index]);
            hero.setAttribute("data-media-url", sources[index]);
        }
        if (counter) {
            counter.textContent = counter.getAttribute("data-template")
                .replace("{current}", String(index + 1))
                .replace("{total}", String(sources.length));
        }
        var dialogImg = qs("[data-pd-lightbox-img]");
        if (dialogImg && dialogImg.closest("dialog").open) {
            dialogImg.src = sources[index];
        }
    }

    function openLightbox(start) {
        var dialog = qs("[data-pd-lightbox]");
        if (!dialog || !sources.length) {
            return;
        }
        setIndex(start == null ? index : start);
        if (typeof dialog.showModal === "function") {
            dialog.showModal();
        } else {
            dialog.setAttribute("open", "open");
        }
        var closeBtn = qs("[data-pd-lightbox-close]", dialog);
        if (closeBtn) {
            closeBtn.focus();
        }
    }

    qsa("[data-pd-open]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var start = parseInt(btn.getAttribute("data-pd-open"), 10);
            openLightbox(isNaN(start) ? 0 : start);
        });
    });

    var lightbox = qs("[data-pd-lightbox]");
    if (lightbox) {
        qsa("[data-pd-lightbox-close]", lightbox).forEach(function (btn) {
            btn.addEventListener("click", function () {
                lightbox.close();
            });
        });
        qsa("[data-pd-lightbox-prev]", lightbox).forEach(function (btn) {
            btn.addEventListener("click", function () {
                setIndex(index - 1);
            });
        });
        qsa("[data-pd-lightbox-next]", lightbox).forEach(function (btn) {
            btn.addEventListener("click", function () {
                setIndex(index + 1);
            });
        });
        lightbox.addEventListener("click", function (event) {
            if (event.target === lightbox) {
                lightbox.close();
            }
        });
    }

    if (gallery && hero && sources.length > 1) {
        var startX = null;
        gallery.addEventListener("touchstart", function (event) {
            startX = event.changedTouches[0].clientX;
        }, { passive: true });
        gallery.addEventListener("touchend", function (event) {
            if (startX == null) {
                return;
            }
            var dx = event.changedTouches[0].clientX - startX;
            startX = null;
            if (Math.abs(dx) < 40) {
                return;
            }
            setIndex(index + (dx < 0 ? 1 : -1));
        }, { passive: true });
    }

    document.addEventListener("keydown", function (event) {
        if (!sources.length) {
            return;
        }
        var open = lightbox && lightbox.open;
        if (event.key === "Escape" && open) {
            lightbox.close();
            return;
        }
        if (!open && event.target && event.target.closest && !event.target.closest("[data-pd-gallery]")) {
            return;
        }
        if (event.key === "ArrowRight") {
            setIndex(index + 1);
        } else if (event.key === "ArrowLeft") {
            setIndex(index - 1);
        }
    });

    var more = qs("[data-pd-more]");
    if (more) {
        var toggle = qs("[data-pd-more-toggle]", more);
        if (toggle) {
            toggle.addEventListener("click", function () {
                var open = more.classList.toggle("is-open");
                toggle.setAttribute("aria-expanded", open ? "true" : "false");
            });
            document.addEventListener("click", function (event) {
                if (!more.contains(event.target)) {
                    more.classList.remove("is-open");
                    toggle.setAttribute("aria-expanded", "false");
                }
            });
        }
    }

    var copyBtn = qs("[data-pd-copy]");
    if (copyBtn && navigator.clipboard) {
        copyBtn.addEventListener("click", function () {
            navigator.clipboard.writeText(copyBtn.getAttribute("data-pd-copy") || window.location.href).then(function () {
                copyBtn.textContent = copyBtn.getAttribute("data-copied-label") || copyBtn.textContent;
            });
        });
    }

    qsa("[data-pd-share]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var title = btn.getAttribute("data-share-title") || document.title;
            var url = btn.getAttribute("data-share-url") || window.location.href;
            if (navigator.share) {
                navigator.share({ title: title, url: url }).catch(function () {});
                return;
            }
            if (navigator.clipboard) {
                navigator.clipboard.writeText(url);
            }
        });
    });

    var copy = qs("[data-pd-description]");
    var moreBtn = qs("[data-pd-description-toggle]");
    if (copy && moreBtn) {
        if (copy.scrollHeight > copy.clientHeight + 8) {
            moreBtn.hidden = false;
        }
        moreBtn.addEventListener("click", function () {
            var collapsed = copy.classList.toggle("is-collapsed");
            moreBtn.textContent = collapsed
                ? moreBtn.getAttribute("data-more")
                : moreBtn.getAttribute("data-less");
        });
    }
})();
