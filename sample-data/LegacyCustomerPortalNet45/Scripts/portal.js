(function () {
    var button = document.getElementById("refreshApi");
    var output = document.getElementById("apiResult");

    if (!button || !output) {
        return;
    }

    button.onclick = function () {
        output.textContent = "Loading /api/orders ...";
        var xhr = new XMLHttpRequest();
        xhr.open("GET", "/api/orders", true);
        xhr.onreadystatechange = function () {
            if (xhr.readyState === 4) {
                output.textContent = xhr.status + "\n" + xhr.responseText;
            }
        };
        xhr.send();
    };
})();
