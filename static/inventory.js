// Load particles.js
particlesJS.load('particles-js', '/static/particles.json', function () {
  console.log('Particles loaded');
});

// Check if admin is logged in
const isAdmin = document.body.getAttribute("data-admin") === "true";
const tableBody = document.getElementById("tableBody");
const searchInput = document.getElementById("searchInput");
let inventoryData = [];

// Load inventory from server
function loadInventory() {
  fetch('/api/inventory')
    .then(res => res.json())
    .then(data => {
      inventoryData = data;
      renderTable(data);
    });
}

// Render inventory table
function renderTable(data) {
  tableBody.innerHTML = "";
  data.forEach((item, index) => {
    const row = document.createElement("tr");

    if (isAdmin) {
      row.innerHTML = `
        <td><input class="edit-field" value="${item.name}" data-index="${index}" data-field="name" /></td>
        <td><input class="edit-field" value="${item.quantity}" data-index="${index}" data-field="quantity" type="number" /></td>
        <td><input class="edit-field" value="${item.price.toFixed(2)}" data-index="${index}" data-field="price" type="number" step="0.01" /></td>
        <td>
          <input class="edit-field" value="${item.url}" data-index="${index}" data-field="url" type="url" />
          <button class="save-btn" onclick="saveRow(${index})">Save</button>
        </td>
      `;
    } else {
      row.innerHTML = `
        <td>${item.name}</td>
        <td>${item.quantity}</td>
        <td>$${item.price.toFixed(2)}</td>
        <td><a href="${item.url}" class="view-button" target="_blank">View</a></td>
      `;
    }

    tableBody.appendChild(row);
  });
}

// Save updated item to server
function saveRow(index) {
  const inputs = document.querySelectorAll(`input[data-index="${index}"]`);
  const updatedItem = { ...inventoryData[index] };

  inputs.forEach(input => {
    const field = input.getAttribute("data-field");
    const value = input.value;

    updatedItem[field] = (field === "quantity" || field === "price")
      ? parseFloat(value)
      : value;
  });

  fetch("/api/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updatedItem)
  })
  .then(res => res.json())
  .then(response => {
    if (response.status === "success") {
      alert("Item updated!");
      loadInventory();
    } else {
      alert("Failed to update item.");
    }
  });
}

// Filter table by search input
searchInput.addEventListener("input", () => {
  const searchTerm = searchInput.value.toLowerCase();
  const filtered = inventoryData.filter(item =>
    item.name.toLowerCase().includes(searchTerm)
  );
  renderTable(filtered);
});

loadInventory();
