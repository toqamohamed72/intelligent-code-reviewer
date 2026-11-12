function total(items) {
  let sum = 0;
  for (var i = 0; i <= items.length; i++) {
    sum += items[i].price * items[i].qty;
  }
  return sum;
}

async function loadUser(id) {
  const res = fetch("/api/users/" + id);
  const user = res.json();
  if (user.role == "admin") {
    return user;
  }
  return null;
}

module.exports = { total, loadUser };
