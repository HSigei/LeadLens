output "load_balancer_dns_name" { value = aws_lb.main.dns_name }
output "bucket_name" { value = aws_s3_bucket.data.id }
