particlesJS.load('particles-js', '/static/particles.json', function () {
  console.log('Particles loaded');
});

let items = [
  { name: "USB-C Cable", quantity: 25, price: 9.99 },
  { name: "Wireless Mouse", quantity: 10, price: 19.99 },
  { name: "Keyboard", quantity: 5, price: 34.99 },
  { name: "Webcam", quantity: 8, price: 49.99 },
  { name: "Monitor Stand", quantity: 3, price: 29.99 },
  { name: "Laptop", quantity: 2, price: 899.00 },
  { name: "External Hard Drive", quantity: 12, price: 74.50 }
];

// The value for isAdmin is now determined by Flask and passed in as a data attribute
let isAdmin = document.body.getAttribute("data-admin") === "true";

const tableBody = document.getElementById("tableBody");
const searchInput = document.getElementById("searchInput");

function renderTable(data) {
  tableBody.innerHTML = "";
  data.forEach((item, index) => {
    const row = document.createElement("tr");

    if (isAdmin) {
      row.innerHTML = `
        <td><input class="edit-field" value="${item.name}" data-index="${index}" data-field="name" /></td>
        <td><input class="edit-field" value="${item.quantity}" data-index="${index}" data-field="quantity" type="number" /></td>
        <td><input class="edit-field" value="${item.price.toFixed(2)}" data-index="${index}" data-field="price" type="number" step="0.01" /></td>
        <td><button class="save-btn" onclick="saveRow(${index})">Save</button></td>
      `;
    } else {
      row.innerHTML = `
        <td>${item.name}</td>
        <td>${item.quantity}</td>
        <td>${item.price.toFixed(2)}</td>
        <td><a href="product.html?id=${index}" class="view-button">View</a></td>
      `;
    }

    tableBody.appendChild(row);
  });
}

function saveRow(index) {
  const inputs = document.querySelectorAll(`input[data-index="${index}"]`);
  inputs.forEach(input => {
    const field = input.getAttribute("data-field");
    const value = input.value;
    items[index][field] = field === "quantity" || field === "price"
      ? parseFloat(value)
      : value;
  });
  renderTable(items);
}

searchInput.addEventListener("input", () => {
  const searchTerm = searchInput.value.toLowerCase();
  const filtered = items.filter(item =>
    item.name.toLowerCase().includes(searchTerm)
  );
  renderTable(filtered);
});

renderTable(items);
