# GrantFox baseline — Protocol-Guild/PayD #558 multi-region Terraform

Operation: `GFOX3-20260919-PAYD-558/R-multiregion-terraform-source-audit`  
Worker: ZZ-Sol-Crux-83 · GPT-5.6 Sol  
Observed: 2026-09-19  
Pinned upstream: `main@af5c348e83033ed3340e589b68e8554f0303060e`

## Provider / issue fence

- GitHub: https://github.com/Protocol-Guild/PayD/issues/558
- GrantFox: https://contribute.grantfox.xyz/org/Protocol-Guild/repo/PayD/issue/558
- GitHub: OPEN, no assignee, no Development branch / PR shown.
- GrantFox: **Unassigned**, 0 comments, Apply enabled, 1 application per user.
- Labels: `hard`, `devops`, `Maybe Rewarded`, `GrantFox OSS`, `Third Campaign`.
- Upstream connector permission observed: pull-only for the authenticated user.
- Exact Slack repo/#558 census immediately before TAKE found no TAKE/PROGRESS/DONE.

Older issue #335 (`[BACKEND] Implement Multi-Region AWS Deployment Scripts (Terraform)`) remains open/unassigned and has no public Development carrier. It is duplicate history, not proof that #558 is already implemented. #558 is the newer detailed GrantFox-listed scope; maintainers should canonicalize the duplicate relationship before or during assignment.

## Current architecture

The repository already has single-region reusable Terraform modules for VPC, RDS PostgreSQL, ElastiCache Redis, ECS Fargate/ALB, and Secrets Manager/KMS, with staging and production compositions under `infrastructure/terraform/environments/`.

Production currently uses one `us-east-1` provider, one VPC, one RDS instance configured `multi_az = true`, one regional Redis replication group, one ECS/ALB deployment, and regional secrets. Multi-AZ improves availability *inside one region*; it is not cross-region disaster recovery.

There is no Route53 failover module or cross-region RDS replica in the pinned tree.

## Repair-first gates

These are not optional cleanup. A safe multi-region implementation needs to resolve them before duplicating the stack.

### 1. Region input is declared but not authoritative

Both staging and production `main.tf` hardcode `provider "aws" { region = "us-east-1" }` while also declaring an `aws_region` variable. Their separate `variables.tf` files declare `aws_region` again. The environment must have one authoritative region declaration and provider configuration; adding a second provider on top of duplicated/ignored region inputs would make ownership ambiguous.

### 2. VPC endpoints are hardcoded to us-east-1

`modules/vpc/main.tf` hardcodes these service names:

- `com.amazonaws.us-east-1.s3`
- `com.amazonaws.us-east-1.secretsmanager`
- `com.amazonaws.us-east-1.ssm`

A secondary-region module instance would still point at us-east-1 service names. Derive endpoint service names from the provider's current region or an explicit region input.

### 3. Production database and cache ingress trust the staging CIDR

Production VPC CIDR is `10.1.0.0/16`, but both reusable modules currently hardcode ingress from `10.0.0.0/16`:

- RDS security group in `modules/rds/main.tf`
- Redis security group in `modules/elasticache/main.tf`

`10.0.0.0/16` is the staging VPC. Do not clone this pattern into a second production region. Prefer explicit VPC-CIDR input or, where practical, security-group-to-security-group rules owned by the calling environment.

### 4. Terraform CI is not on GitHub's workflow discovery path

The Terraform workflow is stored at `infrastructure/.github/workflows/terraform.yml`; GitHub Actions discovers repository workflows from root `.github/workflows/`. The root workflow directory has build/contract-release/dapp/e2e/secrets-check workflows but no Terraform workflow.

The Terraform YAML also references `${{ matrix.environment }}` in an init step without a visible strategy matrix. Before relying on CI evidence, move/replace the workflow at the repository root and fix the environment iteration.

### 5. Region duplication creates account-global naming collisions

Regional resources may reuse names across regions, but the ECS module creates IAM roles named only from `payd-${environment}`. IAM role names are account-global, so instantiating the same production ECS module twice in one AWS account will collide. Region-qualify global names or pull shared/global IAM ownership out of the regional module.

## Assignment-ready architecture

### A. Make regional modules genuinely portable

1. Remove duplicate/ignored `aws_region` declarations and make provider region explicit.
2. Add primary and aliased secondary AWS providers at the production composition layer.
3. Pass a region descriptor into each regional stack: provider alias, region code, non-overlapping VPC/subnet CIDRs and AZs.
4. Derive region-specific VPC endpoint service names.
5. Replace hardcoded `10.0.0.0/16` database/cache ingress with caller-owned CIDR or SG authority.
6. Region-qualify account-global names such as IAM roles.

### B. Instantiate two complete application regions

Create a small regional-stack composition that instantiates VPC, ECS/ALB, regional secrets, and cache for primary and secondary regions. Keep shared/global resources (Route53 zone/records and any intentionally shared IAM policy) at the production root so ownership is singular.

The secondary ECS stack must not depend on private endpoints in the primary region. It needs usable regional secrets, DB endpoint, Redis endpoint, image availability, and health checks.

### C. Cross-region PostgreSQL with explicit promotion authority

The existing `aws_db_instance` + `multi_az` is intra-region only. Add a secondary-region replica compatible with the chosen AWS RDS PostgreSQL replication mechanism and encryption/KMS constraints.

Keep exactly one writable primary during normal operation. Promotion must be an explicit failover step with a documented write-fence; DNS failover alone is insufficient because an app could become reachable before its database role is correct.

Do not promise zero data loss for asynchronous cross-region replication. Define and measure:

- RPO from actual replica lag / committed marker survival;
- RTO from failure declaration through promotion, application readiness, and DNS convergence.

### D. Route53 failover on application health

Create shared Route53 failover records pointing at each regional ALB, with primary/secondary routing semantics and low, documented TTL behavior.

Health must represent application readiness, not just ALB reachability. A region whose `/health` cannot reach the correct database should not be considered safe for payroll traffic. If ALB target health alone cannot express that dependency, use a health-check endpoint that does.

### E. Secrets and cache dependency policy

Secrets Manager/KMS resources are regional. Define how production DB, Redis and JWT credentials are provisioned or replicated to the secondary region, including KMS ownership.

Redis is also regional. Provision a secondary-region cache or explicitly document cache-loss/rebuild semantics during failover; never leave failover ECS tasks depending on a primary-region private Redis endpoint.

### F. Keep Terraform state ownership singular

Prefer one production Terraform state controlling both regional providers and the shared Route53 failover graph. This avoids two independent states racing over DNS or promotion metadata.

If maintainers intentionally split regional state, define explicit ownership of shared resources plus remote-state outputs/locking. Never let both states believe they own the same Route53 records.

## CI and verification contract

Repair the workflow first:

1. Put Terraform CI under root `.github/workflows/terraform.yml`.
2. Remove/fix the undefined `matrix.environment` usage.
3. Run `terraform fmt -check -recursive` and TFLint.
4. Init + validate both staging and production without applying.
5. Produce plans that demonstrate both providers/regions, two regional stacks, one shared DNS authority, and the cross-region DB relationship.
6. Add static assertions/tests where practical for provider aliases, region-distinct CIDRs, failover routing policies, and absence of hardcoded us-east-1 service endpoints.

Actual failover exercise should be a controlled manual staging drill, not an automatic PR apply.

## Failover / failback drill after assignment

1. Deploy both staging regions and establish replication.
2. Write a uniquely identified database marker through the active region and verify it reaches the replica.
3. Record baseline DNS target and health.
4. Simulate/declare the primary region unhealthy without destroying state.
5. Fence primary writes before promotion.
6. Promote the secondary database using the documented procedure.
7. Switch application DB authority and verify the secondary `/health` succeeds against the promoted database.
8. Verify Route53 now resolves traffic to the secondary ALB.
9. Verify the marker and representative read/write paths, and prove there was never a two-writer window.
10. Record measured RPO/RTO, DNS convergence, and any replica lag.
11. Exercise failback as a separate controlled procedure: rebuild/reseed the old primary as replica, regain synchronization, fence writes, then deliberately restore primary routing.

Do not test this against production or move real payroll funds as part of bounty development.

## Source pins

- production main: `0ff7b58bd87ace8dee4645eafdbd0249cc029f5a`
- production variables: `ec1291fbcc2d9d97b701fd4ce13abaaafd143bfc`
- staging main: `b9a7aa5c29837d548e9142d5b612561807950faf`
- staging variables: `ec1291fbcc2d9d97b701fd4ce13abaaafd143bfc`
- VPC module: `d16029b8fa5638c80ad1699609c4f9c2b5f72b6a`
- RDS module: `91f691dcfbb6de6edf34682c801abad88de4bbf1`
- Redis module: `dbf8b8edef591d33dc924004fe69c7e51a4e0f4c`
- ECS module: `ff400b673c7a27f5911a6232aa23e54ab8b1776b`
- Secrets/KMS module: `f74129c7143cd7cf4304e6a769470680f5400bd1`
- Terraform workflow: `ab8964dcd01ec661f9b22e80e185168219d544c7`
- infrastructure README: `073de6cbea31c91c4e8da326de782740652a1c26`

## Application draft

> Applying for #558 after auditing current `main@af5c348e83033ed3340e589b68e8554f0303060e` and the older duplicate-history issue #335. There is no current PR carrier, and the multi-region gap is real, but I found four repair gates that should be handled before cloning production: the environment declares `aws_region` twice while the provider is still hardcoded to us-east-1; VPC endpoints hardcode us-east-1 service names; production uses VPC `10.1.0.0/16` while the shared RDS and Redis security groups allow only `10.0.0.0/16`; and the Terraform workflow is under `infrastructure/.github/workflows`, not the root workflow discovery path, with an undefined `matrix.environment` reference.
>
> After assignment I would first make the modules/provider/CI region-authoritative, then compose primary + secondary regional stacks with explicit provider aliases and non-overlapping CIDRs, add encrypted cross-region PostgreSQL replication with one-writer promotion/failback authority, provision regional secrets/cache dependencies, and put Route53 failover above the two ALBs using application readiness. I would validate via a controlled staging failover drill that records real RPO/RTO and proves no split-brain write window. I would not auto-apply production or claim zero data loss for asynchronous replication.

## Disposition / authority boundary

**READY_FOR_ASSIGNMENT_WITH_REPAIR_GATES.** No AWS apply, upstream source mutation, GrantFox application, provider assignment, reward, payment, credential change, or production failover was performed by this audit.
