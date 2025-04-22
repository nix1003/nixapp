// Load particles.js
particlesJS.load('particles-js', '/static/particles.json', function () {
  console.log('Particles loaded');
});

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
        <td><input class="edit-field" value="${(item.price || 0).toFixed(2)}" data-index="${index}" data-field="price" type="number" step="0.01" /></td>
        <td>
          <input class="edit-field" value="${item.url || ''}" data-index="${index}" data-field="url" type="url" />
          <button class="save-btn" onclick="saveRow(${index})">Save</button>
          <button class="save-btn" style="background-color: crimson;" onclick="deleteRow(${item.id})">Delete</button>
        </td>
      `;
    } else {
      row.innerHTML = `
        <td>${item.name}</td>
        <td>${item.quantity}</td>
        <td>$${(item.price || 0).toFixed(2)}</td>
        <td><a href="${item.url || '#'}" class="view-button" target="_blank">View</a></td>
      `;
    }

    tableBody.appendChild(row);
  });

  if (isAdmin) {
    addAddRow(); // Add blank form row at the bottom
  }
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

// Delete item from server
function deleteRow(id) {
  if (!confirm("Are you sure you want to delete this item?")) return;

  fetch("/api/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: id })
  })
  .then(res => res.json())
  .then(response => {
    if (response.status === "success") {
      alert("Item deleted!");
      loadInventory();
    } else {
      alert("Failed to delete item.");
    }
  });
}

// Add new item row at bottom
function addAddRow() {
  const row = document.createElement("tr");
  row.innerHTML = `
    <td><input class="edit-field" id="new-name" placeholder="Item name" /></td>
    <td><input class="edit-field" id="new-quantity" type="number" placeholder="Qty" /></td>
    <td><input class="edit-field" id="new-price" type="number" step="0.01" placeholder="Price" /></td>
    <td>
      <input class="edit-field" id="new-url" type="url" placeholder="Product URL" />
      <button class="save-btn" onclick="addItem()">Add</button>
    </td>
  `;
  tableBody.appendChild(row);
}

// Add new item to server
function addItem() {
  const name = document.getElementById("new-name").value.trim();
  const quantity = parseInt(document.getElementById("new-quantity").value);
  const price = parseFloat(document.getElementById("new-price").value);
  const url = document.getElementById("new-url").value.trim();

  if (!name || isNaN(quantity)) {
    alert("Please fill out item name and quantity.");
    return;
  }

  const newItem = {
    name,
    quantity,
    price: isNaN(price) ? 0 : price,
    url: url || ""
  };

  fetch("/api/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(newItem)
  })
  .then(res => res.json())
  .then(response => {
    if (response.status === "success") {
      alert("Item added!");
      loadInventory();
    } else {
      alert("Failed to add item.");
    }
  });
}

// Filter table by search
searchInput.addEventListener("input", () => {
  const searchTerm = searchInput.value.toLowerCase();
  const filtered = inventoryData.filter(item =>
    item.name.toLowerCase().includes(searchTerm)
  );
  renderTable(filtered);
});

loadInventory();
