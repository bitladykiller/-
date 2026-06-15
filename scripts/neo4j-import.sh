#!/bin/bash
set -eu

NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
NEO4J_USERNAME="${NEO4J_USERNAME:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-12345678}"
NEO4J_IMPORT_DATA_DIR="${NEO4J_IMPORT_DATA_DIR:-/import-data}"

cypher_shell() {
    command cypher-shell -a "${NEO4J_URI}" -u "${NEO4J_USERNAME}" -p "${NEO4J_PASSWORD}" "$@"
}

REQUIRED_CSV_FILES="
product_nodes.csv
category_nodes.csv
supplier_nodes.csv
customer_nodes.csv
order_nodes.csv
employee_nodes.csv
review_nodes.csv
shipper_nodes.csv
product_category_edges.csv
product_supplier_edges.csv
order_product_edges.csv
customer_order_edges.csv
employee_order_edges.csv
customer_review_edges.csv
review_product_edges.csv
order_shipper_edges.csv
employee_reports_to_edges.csv
"

missing_files=""
for file_name in ${REQUIRED_CSV_FILES}; do
    if [ ! -f "${NEO4J_IMPORT_DATA_DIR}/${file_name}" ]; then
        missing_files="${missing_files} ${file_name}"
    fi
done

if [ -n "${missing_files}" ]; then
    echo "未检测到完整 Neo4j CSV 数据集，跳过导入。"
    echo "缺失文件:${missing_files}"
    echo "如需启用图谱导入，请把 CSV 放到 configs/docker/neo4j-import/ 目录。"
    exit 0
fi

# Neo4j 数据导入脚本
# 等待 Neo4j 服务启动
echo "等待 Neo4j 服务启动..."
until cypher_shell "RETURN 1" > /dev/null 2>&1; do
    sleep 2
done

echo "Neo4j 服务已启动，开始导入数据..."

existing_nodes=$(cypher_shell --format plain "MATCH (n) RETURN count(n) AS node_count;" | awk 'NF {last=$NF} END {print last}')
if [ "${existing_nodes:-0}" != "0" ]; then
    echo "检测到 Neo4j 已存在 ${existing_nodes} 个节点，跳过重复导入。"
    exit 0
fi

# 创建约束和索引
cypher_shell <<EOF2
// 创建唯一性约束
CREATE CONSTRAINT IF NOT EXISTS FOR (p:Product) REQUIRE p.productId IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Category) REQUIRE c.categoryId IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (s:Supplier) REQUIRE s.supplierId IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (cu:Customer) REQUIRE cu.customerId IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (o:Order) REQUIRE o.orderId IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Employee) REQUIRE e.employeeId IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (r:Review) REQUIRE r.reviewId IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (sh:Shipper) REQUIRE sh.shipperId IS UNIQUE;
EOF2

echo "约束创建完成，开始导入节点数据..."

# 导入产品节点
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///product_nodes.csv' AS row
CREATE (p:Product {
    productId: toInteger(row.\`productId:ID(Product)\`),
    productName: row.ProductName,
    supplierId: toInteger(row.SupplierID),
    categoryId: toInteger(row.CategoryID),
    quantityPerUnit: row.QuantityPerUnit,
    unitPrice: toFloat(row.UnitPrice),
    unitsInStock: toInteger(row.UnitsInStock),
    unitsOnOrder: toInteger(row.UnitsOnOrder),
    reorderLevel: toInteger(row.ReorderLevel),
    discontinued: toInteger(row.Discontinued),
    categoryName: row.CategoryName,
    supplierName: row.SupplierName
});
EOF2

echo "产品节点导入完成"

# 导入类别节点
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///category_nodes.csv' AS row
CREATE (c:Category {
    categoryId: toInteger(row.\`categoryId:ID(Category)\`),
    categoryName: row.CategoryName,
    description: row.Description,
    picture: row.Picture
});
EOF2

echo "类别节点导入完成"

# 导入供应商节点
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///supplier_nodes.csv' AS row
CREATE (s:Supplier {
    supplierId: toInteger(row.\`supplierId:ID(Supplier)\`),
    companyName: row.CompanyName,
    contactName: row.ContactName,
    contactTitle: row.ContactTitle,
    address: row.Address,
    city: row.City,
    region: row.Region,
    postalCode: row.PostalCode,
    country: row.Country,
    phone: row.Phone,
    fax: row.Fax,
    homePage: row.HomePage
});
EOF2

echo "供应商节点导入完成"

# 导入客户节点
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///customer_nodes.csv' AS row
CREATE (cu:Customer {
    customerId: row.\`customerId:ID(Customer)\`,
    companyName: row.CompanyName,
    contactName: row.ContactName,
    contactTitle: row.ContactTitle,
    address: row.Address,
    city: row.City,
    region: row.Region,
    postalCode: row.PostalCode,
    country: row.Country,
    phone: row.Phone,
    fax: row.Fax
});
EOF2

echo "客户节点导入完成"

# 导入订单节点
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///order_nodes.csv' AS row
CREATE (o:Order {
    orderId: toInteger(row.\`orderId:ID(Order)\`),
    customerId: row.CustomerID,
    employeeId: toInteger(row.EmployeeID),
    orderDate: row.OrderDate,
    requiredDate: row.RequiredDate,
    shippedDate: row.ShippedDate,
    shipVia: toInteger(row.ShipVia),
    freight: toFloat(row.Freight),
    shipName: row.ShipName,
    shipAddress: row.ShipAddress,
    shipCity: row.ShipCity,
    shipRegion: row.ShipRegion,
    shipPostalCode: row.ShipPostalCode,
    shipCountry: row.ShipCountry
});
EOF2

echo "订单节点导入完成"

# 导入员工节点
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///employee_nodes.csv' AS row
CREATE (e:Employee {
    employeeId: toInteger(row.\`employeeId:ID(Employee)\`),
    lastName: row.LastName,
    firstName: row.FirstName,
    title: row.Title,
    titleOfCourtesy: row.TitleOfCourtesy,
    birthDate: row.BirthDate,
    hireDate: row.HireDate,
    address: row.Address,
    city: row.City,
    region: row.Region,
    postalCode: row.PostalCode,
    country: row.Country,
    homePhone: row.HomePhone,
    extension: row.Extension,
    reportsTo: toInteger(row.ReportsTo)
});
EOF2

echo "员工节点导入完成"

# 导入评论节点
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///review_nodes.csv' AS row
CREATE (r:Review {
    reviewId: toInteger(row.\`reviewId:ID(Review)\`),
    customerId: row.CustomerID,
    productId: toInteger(row.ProductID),
    rating: toInteger(row.Rating),
    reviewDate: row.ReviewDate,
    reviewText: row.ReviewText
});
EOF2

echo "评论节点导入完成"

# 导入物流节点
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///shipper_nodes.csv' AS row
CREATE (sh:Shipper {
    shipperId: toInteger(row.\`shipperId:ID(Shipper)\`),
    companyName: row.CompanyName,
    phone: row.Phone
});
EOF2

echo "物流节点导入完成"

echo "开始导入关系数据..."

# 导入产品-类别关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///product_category_edges.csv' AS row
MATCH (p:Product {productId: toInteger(row.\`:START_ID(Product)\`)})
MATCH (c:Category {categoryId: toInteger(row.\`:END_ID(Category)\`)})
CREATE (p)-[:BELONGS_TO]->(c);
EOF2

echo "产品-类别关系导入完成"

# 导入产品-供应商关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///product_supplier_edges.csv' AS row
MATCH (p:Product {productId: toInteger(row.\`:START_ID(Product)\`)})
MATCH (s:Supplier {supplierId: toInteger(row.\`:END_ID(Supplier)\`)})
CREATE (p)-[:SUPPLIED_BY]->(s);
EOF2

echo "产品-供应商关系导入完成"

# 导入订单-产品关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///order_product_edges.csv' AS row
MATCH (o:Order {orderId: toInteger(row.\`:START_ID(Order)\`)})
MATCH (p:Product {productId: toInteger(row.\`:END_ID(Product)\`)})
CREATE (o)-[:CONTAINS {
    unitPrice: toFloat(row.UnitPrice),
    quantity: toInteger(row.Quantity),
    discount: toFloat(row.Discount)
}]->(p);
EOF2

echo "订单-产品关系导入完成"

# 导入客户-订单关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///customer_order_edges.csv' AS row
MATCH (cu:Customer {customerId: row.\`:START_ID(Customer)\`})
MATCH (o:Order {orderId: toInteger(row.\`:END_ID(Order)\`)})
CREATE (cu)-[:PLACED]->(o);
EOF2

echo "客户-订单关系导入完成"

# 导入员工-订单关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///employee_order_edges.csv' AS row
MATCH (e:Employee {employeeId: toInteger(row.\`:START_ID(Employee)\`)})
MATCH (o:Order {orderId: toInteger(row.\`:END_ID(Order)\`)})
CREATE (e)-[:PROCESSED]->(o);
EOF2

echo "员工-订单关系导入完成"

# 导入客户-评论关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///customer_review_edges.csv' AS row
MATCH (cu:Customer {customerId: row.\`:START_ID(Customer)\`})
MATCH (r:Review {reviewId: toInteger(row.\`:END_ID(Review)\`)})
CREATE (cu)-[:WROTE]->(r);
EOF2

echo "客户-评论关系导入完成"

# 导入评论-产品关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///review_product_edges.csv' AS row
MATCH (r:Review {reviewId: toInteger(row.\`:START_ID(Review)\`)})
MATCH (p:Product {productId: toInteger(row.\`:END_ID(Product)\`)})
CREATE (r)-[:ABOUT]->(p);
EOF2

echo "评论-产品关系导入完成"

# 导入订单-物流关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///order_shipper_edges.csv' AS row
MATCH (o:Order {orderId: toInteger(row.\`:START_ID(Order)\`)})
MATCH (sh:Shipper {shipperId: toInteger(row.\`:END_ID(Shipper)\`)})
CREATE (o)-[:SHIPPED_BY]->(sh);
EOF2

echo "订单-物流关系导入完成"

# 导入员工汇报关系
cypher_shell <<EOF2
LOAD CSV WITH HEADERS FROM 'file:///employee_reports_to_edges.csv' AS row
MATCH (e1:Employee {employeeId: toInteger(row.\`:START_ID(Employee)\`)})
MATCH (e2:Employee {employeeId: toInteger(row.\`:END_ID(Employee)\`)})
CREATE (e1)-[:REPORTS_TO]->(e2);
EOF2

echo "员工汇报关系导入完成"

echo "数据导入完成！"

# 验证数据导入结果
echo "验证数据导入结果..."
cypher_shell <<EOF2
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC;
EOF2
